"""
Callbacks v1 (con `?point_tracked=`, un aviso por articulación).

Desde la v2 el backend solo envía jobs v2, pero los avisos v1 del PACIENTE se
siguen aceptando durante la transición: los jobs enviados por el backend v1 (y
sus reintentos, hasta 72 h) pueden llegar después del despliegue. Los de
REFERENCIA se rechazan (ver ReferenceCallbackTests). Las pruebas del contrato
v2 están en test_callbacks_v2.py.
"""
import json
import time

from django.test import Client, TestCase, override_settings

from telerehabilitation_API.processing.callbacks import INVALID_ANGLES_ERROR
from telerehabilitation_API.processing.hmac_auth import compute_signature
from telerehabilitation_API.processing.results import NO_PERSON_ERROR
from telerehabilitation_API.therapy.models import Exercise, ExerciseResult, ExerciseSkeletonPointTracked
from telerehabilitation_API.therapy.models.exercise_result_point import ExerciseResultPoint

SECRET = 'test-hmac-secret'  # PROCESSING_HMAC_SECRET de settings_test.py


def frames(*angles, fps=30.0):
    """Frames con la forma que manda el servicio (solo los 3 landmarks de la terna)."""
    return [
        {
            'frame': index,
            'timestamp_ms': index / fps * 1000,
            'landmarks': {str(i): {'x': 100.5, 'y': 200.25, 'visibility': 0.99} for i in (11, 13, 15)},
            'angle': angle,
        }
        for index, angle in enumerate(angles)
    ]


def payload(angles=(90.0, 120.5, 45.25), error=None):
    valid = [a for a in angles if a is not None]
    return {
        'error': error,
        'results': {'points': frames(*angles)},
        'max_angle': max(valid) if valid and error is None else None,
        'min_angle': min(valid) if valid and error is None else None,
    }


def service_body(data):
    """Serialización exacta del servicio (callback_delivery.py)."""
    return json.dumps(data, separators=(',', ':')).encode('utf-8')


class CallbackTestCase(TestCase):
    def setUp(self):
        # Sin token y con CSRF activo: así llega el servicio.
        self.http = Client(enforce_csrf_checks=True)
        self.exercise = Exercise.objects.create(
            name='Brazo', description='', order=1, status=Exercise.PROCESSING,
        )
        self.elbow = ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=13)
        self.shoulder = ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=11)

    def send(self, url, data=None, *, body=None, timestamp=None, secret=SECRET, signature=None):
        raw = body if body is not None else service_body(data)
        timestamp = str(int(time.time())) if timestamp is None else timestamp
        signature = signature if signature is not None else compute_signature(timestamp, raw, secret)
        return self.http.post(
            url, data=raw, content_type='application/json',
            HTTP_X_TIMESTAMP=timestamp, HTTP_X_SIGNATURE=signature,
        )


class PatientCallbackTests(CallbackTestCase):
    def setUp(self):
        super().setUp()
        self.result = ExerciseResult.objects.create(exercise=self.exercise, status=ExerciseResult.PROCESSING)

    def url(self, tracked_id, result_id=None):
        return f'/exercise_results/{result_id or self.result.id}/results?point_tracked={tracked_id}'

    def status(self):
        self.result.refresh_from_db()
        return self.result.status

    def test_success_stores_angles_and_series(self):
        response = self.send(self.url(self.elbow.id), payload((90.0, None, 120.5, 45.25)))
        self.assertEqual(response.status_code, 200)

        point = ExerciseResultPoint.objects.get(exercise_result=self.result, point_tracked=self.elbow)
        self.assertEqual((point.max_angle, point.min_angle, point.error), (120.5, 45.25, None))
        self.assertEqual(point.angle_series[:2], [{'t_ms': 0.0, 'angle': 90.0}, {'t_ms': 1000 / 30, 'angle': None}])
        self.assertEqual(len(point.angle_series), 4)

    def test_processed_only_when_all_joints_arrive(self):
        self.send(self.url(self.elbow.id), payload())
        self.assertEqual(self.status(), ExerciseResult.PROCESSING)
        self.send(self.url(self.shoulder.id), payload())
        self.assertEqual(self.status(), ExerciseResult.PROCESSED)

    def test_duplicate_is_idempotent(self):
        for _ in range(2):
            self.assertEqual(self.send(self.url(self.elbow.id), payload()).status_code, 200)
        self.assertEqual(ExerciseResultPoint.objects.filter(exercise_result=self.result).count(), 1)

    def test_null_angles_are_a_joint_error(self):
        response = self.send(self.url(self.elbow.id), payload((None, None)))
        self.assertEqual(response.status_code, 200)
        point = ExerciseResultPoint.objects.get(point_tracked=self.elbow)
        self.assertEqual(point.error, NO_PERSON_ERROR)
        self.assertEqual(self.status(), ExerciseResult.ERROR)

    def test_error_keeps_what_was_measured(self):
        self.send(self.url(self.elbow.id), payload())
        self.send(self.url(self.shoulder.id), payload(error='processing_error: video ilegible'))
        self.assertEqual(self.status(), ExerciseResult.ERROR)
        self.assertEqual(ExerciseResultPoint.objects.get(point_tracked=self.elbow).max_angle, 120.5)
        self.assertIn('ilegible', ExerciseResultPoint.objects.get(point_tracked=self.shoulder).error)

    def test_error_first_then_success_stays_error(self):
        self.send(self.url(self.shoulder.id), payload(error='processing_timeout'))
        self.send(self.url(self.elbow.id), payload())
        self.assertEqual(self.status(), ExerciseResult.ERROR)

    def test_foreign_or_unknown_joint_or_result_is_404(self):
        other = Exercise.objects.create(name='Otro', description='', order=2)
        foreign = ExerciseSkeletonPointTracked.objects.create(exercise=other, skeleton_point_id=25)
        self.assertEqual(self.send(self.url(foreign.id), payload()).status_code, 404)
        self.assertEqual(self.send(self.url(99999), payload()).status_code, 404)
        self.assertEqual(self.send(self.url(self.elbow.id, result_id=99999), payload()).status_code, 404)
        self.assertFalse(ExerciseResultPoint.objects.exists())

    def test_invalid_signature_is_401_and_changes_nothing(self):
        url = self.url(self.elbow.id)
        self.assertEqual(self.send(url, payload(), signature='0' * 64).status_code, 401)
        self.assertEqual(self.send(url, payload(), secret='otro-secreto').status_code, 401)
        self.assertEqual(self.send(url, payload(), timestamp=str(int(time.time()) - 301)).status_code, 401)
        no_headers = self.http.post(url, data=service_body(payload()), content_type='application/json')
        self.assertEqual(no_headers.status_code, 401)
        self.assertFalse(ExerciseResultPoint.objects.exists())
        self.assertEqual(self.status(), ExerciseResult.PROCESSING)

    def test_signature_over_different_bytes_is_401(self):
        # Mismo JSON con otros separadores: la firma es sobre bytes, no sobre el objeto.
        raw = service_body(payload())
        timestamp = str(int(time.time()))
        signature = compute_signature(timestamp, raw, SECRET)
        pretty = json.dumps(payload()).encode()
        response = self.send(self.url(self.elbow.id), body=pretty, timestamp=timestamp, signature=signature)
        self.assertEqual(response.status_code, 401)

    @override_settings(PROCESSING_HMAC_SECRET='')
    def test_without_secret_everything_is_401(self):
        self.assertEqual(self.send(self.url(self.elbow.id), payload(), secret='').status_code, 401)

    def test_unreadable_body_or_missing_point_tracked_is_400(self):
        base = f'/exercise_results/{self.result.id}/results'
        self.assertEqual(self.send(base, payload()).status_code, 400)
        self.assertEqual(self.send(base + '?point_tracked=abc', payload()).status_code, 400)
        self.assertEqual(self.send(self.url(self.elbow.id), body=b'{no es json').status_code, 400)
        self.assertEqual(self.send(self.url(self.elbow.id), body=b'[1, 2]').status_code, 400)
        self.assertEqual(self.send(self.url(self.elbow.id), body=b'\xff\xfe').status_code, 400)

    def test_weird_data_never_500(self):
        cases = [
            {'error': None, 'results': {'points': 'no-lista'}, 'max_angle': 'alto', 'min_angle': 3},
            {'error': None, 'results': None, 'max_angle': True, 'min_angle': 1.0},
            {'error': {'code': 'x'}, 'results': {'points': [1, 'a', {'angle': 3}]}},
            {},
        ]
        for data in cases:
            with self.subTest(data=data):
                response = self.send(self.url(self.elbow.id), data)
                self.assertEqual(response.status_code, 200)
        point = ExerciseResultPoint.objects.get(point_tracked=self.elbow)
        self.assertIsNotNone(point.error)

    def test_invalid_angle_type_is_joint_error(self):
        self.send(self.url(self.elbow.id), {'error': None, 'results': {'points': []},
                                            'max_angle': 'alto', 'min_angle': 3})
        self.assertEqual(ExerciseResultPoint.objects.get(point_tracked=self.elbow).error, INVALID_ANGLES_ERROR)

    def test_nan_is_read_as_null(self):
        body = (b'{"error":null,"results":{"points":[{"frame":0,"timestamp_ms":0.0,'
                b'"landmarks":{},"angle":NaN}]},"max_angle":NaN,"min_angle":10.0}')
        self.assertEqual(self.send(self.url(self.elbow.id), body=body).status_code, 200)
        point = ExerciseResultPoint.objects.get(point_tracked=self.elbow)
        self.assertEqual(point.angle_series, [{'t_ms': 0.0, 'angle': None}])
        self.assertEqual(point.error, NO_PERSON_ERROR)

    def test_long_video_body_over_default_django_limit(self):
        long_payload = payload(tuple(float(i % 180) for i in range(15000)))
        body = service_body(long_payload)
        self.assertGreater(len(body), int(2.5 * 1024 * 1024))
        self.assertEqual(self.send(self.url(self.elbow.id), body=body).status_code, 200)
        self.assertEqual(len(ExerciseResultPoint.objects.get(point_tracked=self.elbow).angle_series), 15000)


class ReferenceCallbackTests(CallbackTestCase):
    """
    Los avisos v1 de REFERENCIA se rechazan desde que las articulaciones se
    conservan al cambiar el video: sin huella del video, uno viejo escribiría
    ángulos de otro video en la misma fila.
    """
    def url(self, tracked_id, exercise_id=None):
        return f'/exercises/{exercise_id or self.exercise.id}/video_results/?point_tracked={tracked_id}'

    def assert_unchanged(self):
        self.exercise.refresh_from_db()
        self.assertEqual(self.exercise.status, Exercise.PROCESSING)
        for tracked in (self.elbow, self.shoulder):
            tracked.refresh_from_db()
            self.assertEqual((tracked.max_angle, tracked.min_angle), (None, None))

    def test_v1_reference_callback_is_404_and_changes_nothing(self):
        self.assertEqual(self.send(self.url(self.elbow.id), payload()).status_code, 404)
        self.assertEqual(self.send(self.url(self.shoulder.id), payload((None,))).status_code, 404)
        self.assertEqual(self.send(f'/exercises/{self.exercise.id}/video_results/', payload()).status_code, 404)
        self.assert_unchanged()

    def test_invalid_signature_is_401_before_anything_else(self):
        # Incluso para un ejercicio inexistente: sin firma no se revela nada.
        response = self.send(self.url(self.elbow.id, exercise_id=99999), payload(), signature='x')
        self.assertEqual(response.status_code, 401)
        response = self.send(self.url(self.elbow.id), payload(), signature='x')
        self.assertEqual(response.status_code, 401)
        self.assert_unchanged()
