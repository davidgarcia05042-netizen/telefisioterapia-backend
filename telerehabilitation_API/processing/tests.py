import json
import os
import re
import tempfile
from types import SimpleNamespace
from unittest import mock

import requests
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from telerehabilitation_API.test_utils import therapist_user
from telerehabilitation_API.processing import client
from telerehabilitation_API.processing.hmac_auth import (
    compute_file_signature, compute_signature, is_valid_signature, signed_headers,
)
from telerehabilitation_API.processing.results import PATIENT, REFERENCE, Target, video_digest
from telerehabilitation_API.therapy.models import (
    Exercise, ExerciseResult, ExerciseSkeletonPointTracked, ScheduledTraining, Training,
)
from telerehabilitation_API.therapy.models.exercise_result_point import ExerciseResultPoint

# settings_test.py: PLATFORM_PUBLIC_URL=https://platform.test,
# PROCESSING_SERVICE_URL=https://processing.test, PROCESSING_HMAC_SECRET=test-hmac-secret.
SECRET = 'test-hmac-secret'
VIDEO_URL = 'https://supabase.test/storage/v1/object/public/videos/results/0b5c3f9e-2a41-4c47-9f0e-1c2d3e4f5a6b.mp4'
VIDEO_BYTES = b'\x00\x00\x00\x18ftypmp42' + b'x' * 1000

DOWNLOAD = 'telerehabilitation_API.processing.client.requests.get'
SEND = 'telerehabilitation_API.processing.client._send_prepared'
SLEEP = 'telerehabilitation_API.processing.client.time.sleep'


class HmacTests(SimpleTestCase):
    def test_plan_vector_matches_service(self):
        """Vector de docs/DECISIONES_TECNICAS.md 4.1, calculado con la función del servicio."""
        body = b'{"error":null,"results":{"points":[]},"max_angle":null,"min_angle":null}'
        self.assertEqual(
            compute_signature('1700000000', body, 'secreto-de-prueba'),
            '7a6b7942aa73175d9dd4ee6d841b42479b87a206434b50fdceccce014be6e5e7',
        )

    def test_file_signature_matches_the_vector(self):
        """La firma leída por bloques da el mismo vector, aunque los bloques corten el cuerpo."""
        body = b'{"error":null,"results":{"points":[]},"max_angle":null,"min_angle":null}'
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, 'cuerpo')
            with open(path, 'wb') as file:
                file.write(body)
            for chunk_bytes in (1, 7, 64, 1024 * 1024):
                self.assertEqual(
                    compute_file_signature('1700000000', path, 'secreto-de-prueba', chunk_bytes=chunk_bytes),
                    '7a6b7942aa73175d9dd4ee6d841b42479b87a206434b50fdceccce014be6e5e7',
                )

    def test_verification(self):
        body = b'{"a":1}'
        signature = compute_signature('1700000000', body, SECRET)
        now = 1700000000 + 120
        self.assertTrue(is_valid_signature('1700000000', signature, body, SECRET, now=now))
        self.assertFalse(is_valid_signature('1700000000', signature[:-1] + '0', body, SECRET, now=now))
        self.assertFalse(is_valid_signature('1700000000', signature, b'{"a":2}', SECRET, now=now))
        self.assertFalse(is_valid_signature('1700000000', signature, body, SECRET, now=1700000000 + 301))
        self.assertFalse(is_valid_signature('1700000000', signature, body, SECRET, now=1700000000 - 301))
        self.assertFalse(is_valid_signature('1700000000', signature, body, 'otro-secreto', now=now))
        self.assertFalse(is_valid_signature('1700000000', signature, body, '', now=now))
        self.assertFalse(is_valid_signature('ayer', signature, body, SECRET, now=now))
        self.assertFalse(is_valid_signature(None, signature, body, SECRET, now=now))
        self.assertFalse(is_valid_signature('1700000000', '', body, SECRET, now=now))
        # Un encabezado no ASCII no debe lanzar excepción (sería un 500).
        self.assertFalse(is_valid_signature('1700000000', 'ñ' * 64, body, SECRET, now=now))


class FakeDownload:
    """Respuesta de requests.get(stream=True) usable como context manager."""

    def __init__(self, content=VIDEO_BYTES, status_code=200, headers=None):
        self.content = content
        self.status_code = status_code
        self.headers = {'Content-Type': 'video/mp4', 'Content-Length': str(len(content))}
        self.headers.update(headers or {})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_content(self, chunk_size):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start:start + chunk_size]


def service_reply(status_code=200, body=None, headers=None):
    response = requests.Response()
    response.status_code = status_code
    response._content = json.dumps(body if body is not None else {}).encode()
    response.headers.update(headers or {})
    return response


def accepted(job_id='job-1'):
    return service_reply(body={'status': 'ok', 'internal_status_code': 'success', 'job_id': job_id})


class Sent:
    """
    Reemplazo de _send_prepared que guarda lo enviado. El cuerpo va en
    streaming desde un archivo temporal que se borra después: se lee al enviar.
    """

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def __call__(self, prepared):
        body = prepared.body if isinstance(prepared.body, bytes) else prepared.body.read()
        self.requests.append(SimpleNamespace(url=prepared.url, headers=dict(prepared.headers), body=body))
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply

    @property
    def last(self):
        return self.requests[-1]


def multipart_field(body, name):
    match = re.search(rb'name="' + name.encode() + rb'"\r\n\r\n(.*?)\r\n--', body, re.S)
    return match.group(1).decode() if match else None


class MultipartTests(SimpleTestCase):
    """El cuerpo y la firma no cambian respecto de los que armaba requests en memoria."""

    BOUNDARY = '0123456789abcdef0123456789abcdef'
    FIELDS = {
        'points': json.dumps([[11, 13, 15], [13, 11, 23]]),
        'exercise': 'exercise:7',
        'resultsEndpoint': 'https://platform.test/exercise_results/5/results?video=abc123',
    }

    def previous_body(self, video_path):
        """Lo que hacía _post_video antes: requests arma el multipart (aquí con boundary fijo)."""
        with open(video_path, 'rb') as video, \
                mock.patch('urllib3.filepost.choose_boundary', return_value=self.BOUNDARY):
            prepared = requests.Request(
                'POST', 'https://processing.test/video/',
                files={'video': ('video.mp4', video, 'video/mp4')}, data=self.FIELDS,
            ).prepare()
        return prepared.body, prepared.headers['Content-Type']

    def test_same_bytes_and_same_signature_as_before(self):
        # Más de un bloque de copia, con bytes de todo tipo (incluidos \r\n y --).
        video_bytes = bytes(range(256)) * 5000 + b'\r\n--' + bytes(range(256)) * 100
        with tempfile.TemporaryDirectory() as folder:
            video_path = os.path.join(folder, 'video.mp4')
            body_path = os.path.join(folder, 'cuerpo.multipart')
            with open(video_path, 'wb') as file:
                file.write(video_bytes)
            previous, previous_type = self.previous_body(video_path)

            with mock.patch.object(client, 'COPY_CHUNK_BYTES', 4096):
                body_type = client.write_multipart(
                    body_path, self.FIELDS, video_path, 'video.mp4', 'video/mp4', boundary=self.BOUNDARY,
                )
            with open(body_path, 'rb') as file:
                written = file.read()

            self.assertEqual(body_type, previous_type)
            self.assertEqual(written, previous)
            timestamp = '1700000000'
            self.assertEqual(
                compute_file_signature(timestamp, body_path, SECRET),
                signed_headers(previous, SECRET, now=1700000000)['X-Signature'],
            )


class ResolvePointsTests(TestCase):
    def setUp(self):
        self.exercise = Exercise.objects.create(name='Codo', description='', order=1)

    def tracked(self, vertex, left=None, right=None):
        return ExerciseSkeletonPointTracked.objects.create(
            exercise=self.exercise, skeleton_point_id=vertex, left_point_id=left, right_point_id=right,
        )

    def test_skeleton_defaults(self):
        self.assertEqual(client.resolve_points(self.tracked(13)), [11, 13, 15])

    def test_exercise_override_wins_over_default(self):
        self.assertEqual(client.resolve_points(self.tracked(13, left=12, right=19)), [12, 13, 19])
        self.assertEqual(client.resolve_points(self.tracked(13, right=21)), [11, 13, 21])

    def test_nose_is_a_valid_neighbor(self):
        # Con `or` en vez de `is not None`, el id 0 se perdería.
        self.assertEqual(client.resolve_points(self.tracked(11, left=0)), [0, 11, 23])

    def test_missing_neighbor_or_repeated_ids(self):
        self.assertIsNone(client.resolve_points(self.tracked(0)))          # NOSE: sin vecinos
        self.assertIsNone(client.resolve_points(self.tracked(13, left=15)))  # [15, 13, 15]
        self.assertIsNone(client.resolve_points(self.tracked(13, left=13)))


class DispatchTests(TestCase):
    """Contrato v2 (docs/DECISIONES_TECNICAS.md, D1): un solo job por video con todas las ternas."""

    def setUp(self):
        self.exercise = Exercise.objects.create(name='Brazo', description='', order=1)
        self.elbow = ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=13)
        self.shoulder = ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=11)
        self.result = ExerciseResult.objects.create(
            exercise=self.exercise, video=VIDEO_URL, status=ExerciseResult.PROCESSING,
        )
        self.target = Target(PATIENT, self.result.id)
        self.digest = video_digest(VIDEO_URL)

    def outcome(self, tracked):
        return ExerciseResultPoint.objects.get(exercise_result=self.result, point_tracked=tracked)

    def refresh_status(self):
        self.result.refresh_from_db()
        return self.result.status

    @mock.patch(SEND, side_effect=Sent(accepted('job-a')))
    @mock.patch(DOWNLOAD, return_value=FakeDownload())
    def test_one_signed_job_with_all_triplets_and_single_download(self, download, send):
        client.dispatch(self.target, VIDEO_URL)

        download.assert_called_once()
        self.assertEqual(download.call_args.kwargs['allow_redirects'], False)
        send.assert_called_once()

        prepared = send.side_effect.last
        self.assertEqual(prepared.url, 'https://processing.test/video/')
        body = prepared.body
        # Va en streaming desde el archivo, con su tamaño y el boundary del cuerpo.
        self.assertEqual(prepared.headers['Content-Length'], str(len(body)))
        boundary = prepared.headers['Content-Type'].split('boundary=')[1]
        self.assertTrue(prepared.headers['Content-Type'].startswith('multipart/form-data; boundary='))
        self.assertTrue(body.startswith(f'--{boundary}\r\n'.encode()))
        self.assertTrue(body.endswith(f'--{boundary}--\r\n'.encode()))
        # La firma verifica con el mismo algoritmo sobre el cuerpo exacto.
        self.assertTrue(is_valid_signature(
            prepared.headers['X-Timestamp'], prepared.headers['X-Signature'], body, SECRET,
        ))
        # Ternas en el orden de los id de las articulaciones (codo, hombro).
        self.assertEqual(json.loads(multipart_field(body, 'points')), [[11, 13, 15], [13, 11, 23]])
        self.assertEqual(multipart_field(body, 'exercise'), f'exercise:{self.exercise.id}')
        self.assertEqual(
            multipart_field(body, 'resultsEndpoint'),
            f'https://platform.test/exercise_results/{self.result.id}/results?video={self.digest}',
        )
        self.assertNotIn(b'point_tracked', body)
        self.assertIn(b'filename="video.mp4"\r\nContent-Type: video/mp4', body)
        self.assertIn(VIDEO_BYTES, body)

        # Aceptado: nada registrado todavía, sigue procesando hasta el callback.
        self.assertFalse(ExerciseResultPoint.objects.exists())
        self.assertEqual(self.refresh_status(), ExerciseResult.PROCESSING)

    @mock.patch(SEND, side_effect=Sent(accepted()))
    @mock.patch(DOWNLOAD, return_value=FakeDownload())
    def test_a_single_joint_is_still_sent_in_v2_form(self, download, send):
        self.shoulder.delete()
        client.dispatch(self.target, VIDEO_URL)
        # Lista de ternas aunque sea una: así el callback siempre es v2.
        self.assertEqual(json.loads(multipart_field(send.side_effect.last.body, 'points')), [[11, 13, 15]])

    @mock.patch(SEND, side_effect=Sent(accepted()))
    @mock.patch(DOWNLOAD, return_value=FakeDownload())
    def test_repeated_triplet_is_sent_once(self, download, send):
        ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=13)
        client.dispatch(self.target, VIDEO_URL)
        self.assertEqual(json.loads(multipart_field(send.side_effect.last.body, 'points')),
                         [[11, 13, 15], [13, 11, 23]])

    @mock.patch(SEND, side_effect=Sent(accepted()))
    @mock.patch(DOWNLOAD, return_value=FakeDownload())
    def test_reference_endpoint_has_trailing_slash_and_no_point_tracked(self, download, send):
        self.exercise.video = VIDEO_URL
        self.exercise.save()
        client.dispatch(Target(REFERENCE, self.exercise.id), VIDEO_URL)
        self.assertEqual(
            multipart_field(send.side_effect.last.body, 'resultsEndpoint'),
            f'https://platform.test/exercises/{self.exercise.id}/video_results/?video={self.digest}',
        )

    @mock.patch(SEND, side_effect=Sent(accepted()))
    @mock.patch(DOWNLOAD, return_value=FakeDownload())
    def test_joint_without_neighbors_is_error_and_left_out_of_the_job(self, download, send):
        nose = ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=0)
        client.dispatch(self.target, VIDEO_URL)

        send.assert_called_once()
        self.assertEqual(json.loads(multipart_field(send.side_effect.last.body, 'points')),
                         [[11, 13, 15], [13, 11, 23]])
        self.assertEqual(self.outcome(nose).error, client.MISSING_NEIGHBORS_ERROR)
        self.assertEqual(self.refresh_status(), ExerciseResult.ERROR)

    @mock.patch(SEND, return_value=service_reply(
        body={'status': 'error', 'internal_status_code': 'undecodable_video', 'job_id': None}))
    @mock.patch(DOWNLOAD, return_value=FakeDownload())
    def test_service_rejection_with_http_200_puts_every_joint_in_error(self, download, send):
        client.dispatch(self.target, VIDEO_URL)
        self.assertIn('undecodable_video', self.outcome(self.elbow).error)
        self.assertIn('undecodable_video', self.outcome(self.shoulder).error)
        self.assertEqual(self.refresh_status(), ExerciseResult.ERROR)

    @mock.patch(SEND, return_value=service_reply(500))
    @mock.patch(DOWNLOAD, return_value=FakeDownload())
    def test_http_error_puts_every_joint_in_error(self, download, send):
        client.dispatch(self.target, VIDEO_URL)
        self.assertIn('HTTP 500', self.outcome(self.elbow).error)
        self.assertIn('HTTP 500', self.outcome(self.shoulder).error)

    @mock.patch(SEND, side_effect=requests.ConnectionError('down'))
    @mock.patch(DOWNLOAD, return_value=FakeDownload())
    def test_network_error_puts_every_joint_in_error(self, download, send):
        client.dispatch(self.target, VIDEO_URL)
        self.assertIn('No se pudo contactar', self.outcome(self.elbow).error)
        self.assertIn('No se pudo contactar', self.outcome(self.shoulder).error)

    @mock.patch(SLEEP)
    @mock.patch(SEND, side_effect=Sent(service_reply(429, headers={'Retry-After': '7'}), accepted('a')))
    @mock.patch(DOWNLOAD, return_value=FakeDownload())
    def test_rate_limit_is_retried_once(self, download, send, sleep):
        client.dispatch(self.target, VIDEO_URL)
        first, second = send.side_effect.requests
        self.assertEqual(first.body, second.body)
        for sent in (first, second):
            self.assertTrue(is_valid_signature(sent.headers['X-Timestamp'], sent.headers['X-Signature'], sent.body, SECRET))
        sleep.assert_called_once_with(7)
        self.assertEqual(send.call_count, 2)
        self.assertFalse(ExerciseResultPoint.objects.exists())

    @mock.patch(SLEEP)
    @mock.patch(SEND, return_value=service_reply(429, headers={'Retry-After': '9999'}))
    @mock.patch(DOWNLOAD, return_value=FakeDownload())
    def test_rate_limit_twice_is_an_error(self, download, send, sleep):
        client.dispatch(self.target, VIDEO_URL)
        sleep.assert_called_with(client.MAX_RETRY_AFTER_SECONDS)
        self.assertIn('HTTP 429', self.outcome(self.elbow).error)

    @mock.patch(SEND)
    @mock.patch(DOWNLOAD, return_value=FakeDownload(headers={'Content-Length': str(51 * 1024 * 1024)}))
    def test_declared_size_over_limit(self, download, send):
        client.dispatch(self.target, VIDEO_URL)
        send.assert_not_called()
        self.assertIn('50 MB', self.outcome(self.elbow).error)
        self.assertIn('50 MB', self.outcome(self.shoulder).error)

    @mock.patch(SEND)
    @mock.patch('telerehabilitation_API.processing.client.MAX_VIDEO_BYTES', 500)
    @mock.patch(DOWNLOAD, return_value=FakeDownload(headers={'Content-Length': ''}))
    def test_streamed_size_over_limit(self, download, send):
        client.dispatch(self.target, VIDEO_URL)
        send.assert_not_called()
        self.assertIn('50 MB', self.outcome(self.elbow).error)

    @mock.patch(SEND)
    @mock.patch(DOWNLOAD, return_value=FakeDownload(status_code=404))
    def test_download_failure(self, download, send):
        client.dispatch(self.target, VIDEO_URL)
        send.assert_not_called()
        self.assertIn('HTTP 404', self.outcome(self.elbow).error)
        self.assertEqual(self.refresh_status(), ExerciseResult.ERROR)

    @mock.patch(SEND)
    @mock.patch(DOWNLOAD, side_effect=requests.Timeout('slow'))
    def test_download_timeout(self, download, send):
        client.dispatch(self.target, VIDEO_URL)
        self.assertIn('Timeout', self.outcome(self.elbow).error)

    @mock.patch(SEND, return_value=accepted())
    @mock.patch(DOWNLOAD, return_value=FakeDownload())
    def test_temp_file_is_removed(self, download, send):
        created = []
        real_mkstemp = client.tempfile.mkstemp

        def tracking_mkstemp(**kwargs):
            fd, path = real_mkstemp(**kwargs)
            created.append(path)
            return fd, path

        with mock.patch.object(client.tempfile, 'mkstemp', side_effect=tracking_mkstemp):
            client.dispatch(self.target, VIDEO_URL)
        # El video descargado y el multipart que se envía.
        self.assertEqual(len(created), 2)
        self.assertTrue(created[0].endswith('.mp4'))
        self.assertTrue(created[1].endswith('.multipart'))
        self.assertFalse(any(os.path.exists(path) for path in created))

    @mock.patch(SEND, side_effect=requests.ConnectionError('down'))
    @mock.patch(DOWNLOAD, return_value=FakeDownload())
    def test_temp_files_are_removed_when_sending_fails(self, download, send):
        created = []
        real_mkstemp = client.tempfile.mkstemp

        def tracking_mkstemp(**kwargs):
            fd, path = real_mkstemp(**kwargs)
            created.append(path)
            return fd, path

        with mock.patch.object(client.tempfile, 'mkstemp', side_effect=tracking_mkstemp):
            client.dispatch(self.target, VIDEO_URL)
        self.assertEqual(len(created), 2)
        self.assertFalse(any(os.path.exists(path) for path in created))

    @mock.patch(SEND, return_value=accepted())
    @mock.patch(DOWNLOAD, return_value=FakeDownload(status_code=404))
    def test_temp_file_is_removed_on_download_error(self, download, send):
        with mock.patch.object(client.os, 'remove', wraps=os.remove) as remove:
            client.dispatch(self.target, VIDEO_URL)
        remove.assert_called_once()

    @mock.patch(SEND)
    @mock.patch(DOWNLOAD)
    def test_exercise_without_joints_goes_to_error(self, download, send):
        ExerciseSkeletonPointTracked.objects.all().delete()
        client.dispatch(self.target, VIDEO_URL)
        download.assert_not_called()
        self.assertEqual(self.refresh_status(), ExerciseResult.ERROR)

    @override_settings(PROCESSING_HMAC_SECRET='')
    @mock.patch(SEND)
    @mock.patch(DOWNLOAD)
    def test_missing_configuration_marks_joints_as_error(self, download, send):
        client.dispatch(self.target, VIDEO_URL)
        download.assert_not_called()
        self.assertIn('PROCESSING_HMAC_SECRET', self.outcome(self.elbow).error)

    @mock.patch(SEND, return_value=service_reply(500))
    @mock.patch(DOWNLOAD, return_value=FakeDownload())
    def test_reference_send_failure_puts_exercise_in_error(self, download, send):
        self.exercise.status = Exercise.PROCESSING
        self.exercise.save()
        client.dispatch(Target(REFERENCE, self.exercise.id), VIDEO_URL)
        self.exercise.refresh_from_db()
        self.assertEqual(self.exercise.status, Exercise.ERROR)


class DispatchAfterCommitTests(TestCase):
    @mock.patch('telerehabilitation_API.processing.client.threading.Thread')
    def test_thread_starts_only_after_commit(self, thread):
        target = Target(PATIENT, 1)
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            client.dispatch_after_commit(target, VIDEO_URL)
        thread.assert_not_called()
        callbacks[0]()
        thread.assert_called_once()
        self.assertEqual(thread.call_args.kwargs['args'], (target, VIDEO_URL))
        # No daemon: un worker que gunicorn recicla o detiene espera a que el envío termine.
        self.assertFalse(thread.call_args.kwargs['daemon'])
        thread.return_value.start.assert_called_once()

    @mock.patch('telerehabilitation_API.processing.client.connection')
    @mock.patch('telerehabilitation_API.processing.client.dispatch', side_effect=RuntimeError('boom'))
    def test_thread_body_logs_and_closes_connection(self, dispatch, connection):
        with self.assertLogs('telerehabilitation_API.processing.client', 'ERROR'):
            client._dispatch_in_thread(Target(PATIENT, 1), VIDEO_URL)
        connection.close.assert_called_once()


DISPATCH_AFTER_COMMIT = 'telerehabilitation_API.processing.client.dispatch_after_commit'


class TriggerViewTests(TestCase):
    def setUp(self):
        self.api = APIClient()
        self.api.force_authenticate(therapist_user())
        self.exercise = Exercise.objects.create(name='Codo', description='', order=1)
        scheduled = ScheduledTraining.objects.create(start_time=timezone.now())
        self.training = Training.objects.create(schedule_training=scheduled)

    def test_exercise_results_and_training_require_token(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.post('/exercise_results', {}, format='json').status_code, 401)
        self.assertEqual(anonymous.post('/training', {}, format='json').status_code, 401)
        # Desde M1 (docs/DECISIONES_TECNICAS.md) el GET también exige sesión.
        self.assertEqual(anonymous.get('/training').status_code, 401)

    @mock.patch(DISPATCH_AFTER_COMMIT)
    def test_patient_video_creates_result_and_dispatches(self, dispatch):
        response = self.api.post('/exercise_results', {
            'video': VIDEO_URL, 'training_id': self.training.id, 'exercise_id': self.exercise.id,
        }, format='json')
        self.assertEqual(response.status_code, 201, response.content)
        result = ExerciseResult.objects.get(pk=response.json()['id'])
        self.assertEqual(result.status, ExerciseResult.PROCESSING)
        self.assertEqual(result.video, VIDEO_URL)
        dispatch.assert_called_once_with(Target(PATIENT, result.id), VIDEO_URL)

    @mock.patch(DISPATCH_AFTER_COMMIT)
    def test_patient_video_with_unknown_ids_is_400(self, dispatch):
        response = self.api.post('/exercise_results', {
            'video': VIDEO_URL, 'training_id': 9999, 'exercise_id': 'abc',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertFalse(ExerciseResult.objects.exists())
        dispatch.assert_not_called()

    @override_settings(PROCESSING_SERVICE_URL='')
    @mock.patch(DISPATCH_AFTER_COMMIT)
    def test_patient_video_without_service_config_is_503(self, dispatch):
        response = self.api.post('/exercise_results', {
            'video': VIDEO_URL, 'training_id': self.training.id, 'exercise_id': self.exercise.id,
        }, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertIn('PROCESSING_SERVICE_URL', response.json()['error'])
        self.assertFalse(ExerciseResult.objects.exists())

    @mock.patch(DISPATCH_AFTER_COMMIT)
    def test_reference_video_updates_joints_and_dispatches(self, dispatch):
        url = VIDEO_URL.replace('/results/', '/exercises/')
        response = self.api.post(f'/exercises/{self.exercise.id}/video/', {
            'video': url,
            'points': json.dumps([{'skeleton_point_id': 13}, {'skeleton_point_id': 25, 'left_point_id': 24}]),
        }, format='json')
        self.assertEqual(response.status_code, 200, response.content)
        self.exercise.refresh_from_db()
        self.assertEqual(self.exercise.status, Exercise.PROCESSING)
        # Desde cuándo procesa: con esto pasa a error si no llega el callback (stale.py).
        self.assertIsNotNone(self.exercise.processing_started_at)
        self.assertEqual(self.exercise.tracked_points.count(), 2)
        dispatch.assert_called_once_with(Target(REFERENCE, self.exercise.id), url)

    @override_settings(PROCESSING_HMAC_SECRET='')
    @mock.patch(DISPATCH_AFTER_COMMIT)
    def test_reference_video_without_service_config_keeps_joints(self, dispatch):
        ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=13)
        response = self.api.post(f'/exercises/{self.exercise.id}/video/', {
            'video': VIDEO_URL, 'points': '25',
        }, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(list(self.exercise.tracked_points.values_list('skeleton_point_id', flat=True)), [13])
        dispatch.assert_not_called()
