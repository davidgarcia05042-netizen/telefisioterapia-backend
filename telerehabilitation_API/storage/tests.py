import re
from unittest import mock
from urllib.parse import parse_qs, urlsplit

import requests
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from rest_framework import serializers
from rest_framework.test import APIClient

from telerehabilitation_API.test_utils import therapist_user, user_in_group
from telerehabilitation_API.storage import backend, r2, supabase
from telerehabilitation_API.therapy.models import Exercise, ExerciseSkeletonPointTracked

# settings_test.py fija SUPABASE_URL=https://supabase.test y SUPABASE_BUCKET=videos.
PUBLIC_PREFIX = 'https://supabase.test/storage/v1/object/public/videos/'
VALID_VIDEO_URL = PUBLIC_PREFIX + 'results/0b5c3f9e-2a41-4c47-9f0e-1c2d3e4f5a6b.mp4'
UUID_PATH = re.compile(r'^(exercises|results)/[0-9a-f-]{36}\.(mp4|mov|webm|mkv|avi)$')

POST_TARGET = 'telerehabilitation_API.storage.supabase.requests.post'


def supabase_reply(status_code=200, body=None):
    response = mock.Mock(status_code=status_code, ok=200 <= status_code < 300, text='')
    response.json.return_value = body if body is not None else {}
    return response


def signed_reply_for(url, **kwargs):
    """Simula a Supabase: devuelve la url RELATIVA a /storage/v1, como la API real."""
    relative = url.split('/storage/v1', 1)[1]
    return supabase_reply(body={'url': f'{relative}?token=tok123', 'token': 'tok123'})


class UploadUrlViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user = therapist_user()
        self.client.force_authenticate(user)

    def post(self, **data):
        body = {'kind': 'result', 'content_type': 'video/mp4', 'filename': 'clip.mp4'}
        body.update(data)
        return self.client.post('/storage/upload_url', body, format='json')

    def test_requires_authentication(self):
        response = APIClient().post('/storage/upload_url', {}, format='json')
        self.assertEqual(response.status_code, 401)

    @mock.patch(POST_TARGET, side_effect=signed_reply_for)
    def test_success_builds_absolute_upload_url(self, post):
        response = self.post()
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()

        self.assertRegex(data['path'], UUID_PATH)
        self.assertTrue(data['path'].startswith('results/'))
        sign_endpoint = f'https://supabase.test/storage/v1/object/upload/sign/videos/{data["path"]}'
        self.assertEqual(post.call_args.args[0], sign_endpoint)
        self.assertEqual(data['upload_url'], sign_endpoint + '?token=tok123')
        self.assertEqual(data['public_url'], PUBLIC_PREFIX + data['path'])

    @mock.patch(POST_TARGET, side_effect=signed_reply_for)
    def test_new_secret_key_goes_only_in_apikey(self, post):
        self.post()
        headers = post.call_args.kwargs['headers']
        self.assertEqual(headers, {'apikey': 'test-supabase-secret'})

    @override_settings(SUPABASE_SECRET_KEY='eyJlegacy.jwt.key')
    @mock.patch(POST_TARGET, side_effect=signed_reply_for)
    def test_legacy_jwt_key_also_goes_as_bearer(self, post):
        self.post()
        headers = post.call_args.kwargs['headers']
        self.assertEqual(headers['apikey'], 'eyJlegacy.jwt.key')
        self.assertEqual(headers['Authorization'], 'Bearer eyJlegacy.jwt.key')

    @mock.patch(POST_TARGET, side_effect=signed_reply_for)
    def test_only_extension_of_original_name_is_kept(self, post):
        response = self.post(kind='exercise', content_type='video/quicktime',
                             filename='Juan Pérez CC 1020304050.MOV')
        path = response.json()['path']
        self.assertRegex(path, r'^exercises/[0-9a-f-]{36}\.mov$')
        self.assertNotIn('Juan', post.call_args.args[0])

    @mock.patch(POST_TARGET, side_effect=signed_reply_for)
    def test_extension_falls_back_to_content_type(self, post):
        for filename in ('sin_extension', 'nota.txt', ''):
            path = self.post(content_type='video/webm', filename=filename).json()['path']
            self.assertTrue(path.endswith('.webm'), path)

    @mock.patch(POST_TARGET)
    def test_rejects_unknown_content_type_or_kind(self, post):
        self.assertEqual(self.post(content_type='image/png').status_code, 400)
        self.assertEqual(self.post(kind='avatar').status_code, 400)
        post.assert_not_called()

    @override_settings(SUPABASE_SECRET_KEY='')
    @mock.patch(POST_TARGET)
    def test_missing_configuration_gives_clear_503(self, post):
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertIn('SUPABASE_SECRET_KEY', response.json()['error'])
        post.assert_not_called()

    @mock.patch(POST_TARGET, return_value=supabase_reply(400, {'message': 'Bucket not found'}))
    def test_supabase_error_gives_502(self, post):
        with self.assertLogs('telerehabilitation_API.storage.views', 'ERROR'):
            self.assertEqual(self.post().status_code, 502)

    @mock.patch(POST_TARGET, side_effect=requests.ConnectionError('down'))
    def test_supabase_unreachable_gives_502(self, post):
        with self.assertLogs('telerehabilitation_API.storage.views', 'ERROR'):
            self.assertEqual(self.post().status_code, 502)

    @mock.patch(POST_TARGET, return_value=supabase_reply(200, {'token': 'sin-url'}))
    def test_reply_without_url_gives_502(self, post):
        with self.assertLogs('telerehabilitation_API.storage.views', 'ERROR'):
            self.assertEqual(self.post().status_code, 502)


R2_SETTINGS = {
    'STORAGE_BACKEND': 'r2',
    'R2_ACCOUNT_ID': 'test-account',
    'R2_ACCESS_KEY_ID': 'test-access-key',
    'R2_SECRET_ACCESS_KEY': 'test-secret-key',
    'R2_BUCKET': 'test-bucket',
    'R2_PUBLIC_BASE_URL': 'https://pub-test.r2.dev',
}
R2_PUBLIC_PREFIX = 'https://pub-test.r2.dev/'
# Cualquier envío HTTP de botocore. Firmar una URL es un cálculo local: si algo
# intentara hablar con R2, la prueba falla en vez de salir a la red.
BOTOCORE_SEND = 'botocore.httpsession.URLLib3Session.send'


@override_settings(**R2_SETTINGS)
@mock.patch(BOTOCORE_SEND, side_effect=AssertionError('las pruebas no llaman a R2'))
@mock.patch(POST_TARGET, side_effect=AssertionError('con STORAGE_BACKEND=r2 no se llama a Supabase'))
class R2UploadUrlViewTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.client.force_authenticate(therapist_user())

    def post(self, **data):
        body = {'kind': 'result', 'content_type': 'video/mp4', 'filename': 'clip.mp4'}
        body.update(data)
        return self.client.post('/storage/upload_url', body, format='json')

    def test_same_response_shape_with_presigned_put(self, supabase_post, botocore_send):
        response = self.post(kind='exercise', content_type='video/quicktime', filename='Juan Pérez.MOV')
        self.assertEqual(response.status_code, 200, response.content)
        data = response.json()
        self.assertEqual(set(data), {'upload_url', 'public_url', 'path'})
        self.assertRegex(data['path'], r'^exercises/[0-9a-f-]{36}\.mov$')
        self.assertEqual(data['public_url'], R2_PUBLIC_PREFIX + data['path'])

        url = urlsplit(data['upload_url'])
        query = parse_qs(url.query)
        self.assertEqual(url.scheme, 'https')
        self.assertEqual(url.netloc, 'test-account.r2.cloudflarestorage.com')
        self.assertEqual(url.path, f'/test-bucket/{data["path"]}')
        self.assertEqual(query['X-Amz-Algorithm'], ['AWS4-HMAC-SHA256'])
        self.assertIn('/auto/s3/aws4_request', query['X-Amz-Credential'][0])
        self.assertEqual(query['X-Amz-Expires'], [str(r2.UPLOAD_URL_EXPIRES_SECONDS)])
        self.assertEqual(r2.UPLOAD_URL_EXPIRES_SECONDS, 7200)
        # El Content-Type va firmado: un PUT con otro tipo no sirve.
        self.assertIn('content-type', query['X-Amz-SignedHeaders'][0].split(';'))
        # Sin sumas de verificación que el navegador no sabría calcular.
        self.assertFalse([k for k in query if 'checksum' in k.lower()], query)
        # Ni el nombre original ni la clave secreta viajan en la URL.
        self.assertNotIn('Juan', data['upload_url'])
        self.assertNotIn('test-secret-key', data['upload_url'])

    def test_signature_depends_on_content_type(self, supabase_post, botocore_send):
        mp4 = r2.create_signed_upload_url('results/a.mp4', 'video/mp4')
        webm = r2.create_signed_upload_url('results/a.mp4', 'video/webm')
        self.assertNotEqual(parse_qs(urlsplit(mp4).query)['X-Amz-Signature'],
                            parse_qs(urlsplit(webm).query)['X-Amz-Signature'])

    @override_settings(R2_SECRET_ACCESS_KEY='', R2_PUBLIC_BASE_URL='')
    def test_missing_r2_configuration_gives_clear_503(self, supabase_post, botocore_send):
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertIn('R2_SECRET_ACCESS_KEY', response.json()['error'])
        self.assertIn('R2_PUBLIC_BASE_URL', response.json()['error'])

    def test_signing_error_gives_502(self, supabase_post, botocore_send):
        from botocore.exceptions import NoCredentialsError

        broken = mock.Mock()
        broken.generate_presigned_url.side_effect = NoCredentialsError()
        with mock.patch.object(r2, 'client', return_value=broken), \
                self.assertLogs('telerehabilitation_API.storage.views', 'ERROR'):
            self.assertEqual(self.post().status_code, 502)

    def test_patient_still_cannot_upload_reference_videos(self, supabase_post, botocore_send):
        client = APIClient()
        client.force_authenticate(user_in_group('Patient'))
        response = client.post('/storage/upload_url',
                               {'kind': 'exercise', 'content_type': 'video/mp4'}, format='json')
        self.assertEqual(response.status_code, 403)


class VideoUrlValidatorTests(SimpleTestCase):
    def test_accepts_object_of_own_bucket(self):
        backend.validate_public_video_url(VALID_VIDEO_URL)

    def test_rejects_foreign_or_tricky_urls(self):
        rejected = [
            'https://example.com/video.mp4',
            'http://169.254.169.254/latest/meta-data/',
            'https://supabase.test/storage/v1/object/public/otro-bucket/a.mp4',
            'https://supabase.test/storage/v1/object/authenticated/videos/a.mp4',
            'https://supabase.test.evil.com/storage/v1/object/public/videos/a.mp4',
            'https://supabase.test@evil.com/storage/v1/object/public/videos/a.mp4',
            PUBLIC_PREFIX,
            PUBLIC_PREFIX + '../otro-bucket/a.mp4',
            PUBLIC_PREFIX + '%2e%2e/otro-bucket/a.mp4',
            PUBLIC_PREFIX + 'results\\..\\a.mp4',
            VALID_VIDEO_URL + '?download=1',
            VALID_VIDEO_URL + '#t=10',
        ]
        for url in rejected:
            with self.subTest(url=url), self.assertRaises(serializers.ValidationError):
                backend.validate_public_video_url(url)

    @override_settings(SUPABASE_URL='')
    def test_rejects_everything_when_not_configured(self):
        with self.assertRaises(serializers.ValidationError):
            backend.validate_public_video_url(VALID_VIDEO_URL)

    def test_r2_urls_are_rejected_while_r2_is_not_configured(self):
        with self.assertRaises(serializers.ValidationError):
            backend.validate_public_video_url(R2_PUBLIC_PREFIX + 'results/0b5c3f9e-2a41-4c47-9f0e-1c2d3e4f5a6b.mp4')


R2_VIDEO_URL = R2_PUBLIC_PREFIX + 'results/0b5c3f9e-2a41-4c47-9f0e-1c2d3e4f5a6b.mp4'


class R2VideoUrlValidatorTests(SimpleTestCase):
    """Transición: se aceptan el bucket de Supabase y el de R2, y nada más."""

    @override_settings(**R2_SETTINGS)
    def test_accepts_r2_host_and_still_supabase(self):
        backend.validate_public_video_url(R2_VIDEO_URL)
        backend.validate_public_video_url(VALID_VIDEO_URL)

    @override_settings(STORAGE_BACKEND='supabase', R2_PUBLIC_BASE_URL='https://pub-test.r2.dev')
    def test_accepts_r2_even_when_supabase_is_the_active_backend(self):
        backend.validate_public_video_url(R2_VIDEO_URL)

    @override_settings(**R2_SETTINGS, SUPABASE_URL='')
    def test_accepts_r2_alone(self):
        backend.validate_public_video_url(R2_VIDEO_URL)
        with self.assertRaises(serializers.ValidationError):
            backend.validate_public_video_url(VALID_VIDEO_URL)

    @override_settings(**R2_SETTINGS)
    def test_rejects_foreign_hosts_and_misleading_prefixes(self):
        rejected = [
            'https://example.com/results/a.mp4',
            'https://pub-otro.r2.dev/results/a.mp4',
            'https://test-account.r2.cloudflarestorage.com/test-bucket/results/a.mp4',
            # Prefijos engañosos: empiezan igual que el host propio.
            'https://pub-test.r2.dev.evil.com/results/a.mp4',
            'https://pub-test.r2.dev@evil.com/results/a.mp4',
            'https://pub-test.r2.devx/results/a.mp4',
            'http://pub-test.r2.dev/results/a.mp4',
            R2_PUBLIC_PREFIX,
            R2_PUBLIC_PREFIX + '../a.mp4',
            R2_PUBLIC_PREFIX + '%2e%2e/a.mp4',
            R2_PUBLIC_PREFIX + 'results\\..\\a.mp4',
            R2_VIDEO_URL + '?X-Amz-Signature=abc',
            R2_VIDEO_URL + '#t=10',
        ]
        for url in rejected:
            with self.subTest(url=url), self.assertRaises(serializers.ValidationError):
                backend.validate_public_video_url(url)

    @override_settings(**{**R2_SETTINGS, 'R2_PUBLIC_BASE_URL': 'https://pub-test.r2.dev'})
    def test_base_url_is_used_without_trailing_slash(self):
        # settings.py quita la barra final; el prefijo agrega exactamente una.
        self.assertEqual(r2.public_url('results/a.mp4'), 'https://pub-test.r2.dev/results/a.mp4')


class VideoEndpointsRejectForeignUrlsTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        user = therapist_user()
        self.client.force_authenticate(user)
        self.exercise = Exercise.objects.create(name='Flexión de codo', description='', order=1)
        ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=13)

    def test_exercise_result_with_foreign_url_is_400(self):
        response = self.client.post('/exercise_results', {
            'video': 'https://example.com/video.mp4', 'training_id': 1, 'exercise_id': self.exercise.id,
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertIn('bucket', str(response.json()))

    def test_exercise_video_with_foreign_url_is_400_and_keeps_tracked_points(self):
        response = self.client.post(f'/exercises/{self.exercise.id}/video/', {
            'video': 'https://example.com/video.mp4', 'points': '13',
        }, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.exercise.tracked_points.count(), 1)
        self.exercise.refresh_from_db()
        self.assertIsNone(self.exercise.video)

    def test_exercise_video_with_own_url_is_saved(self):
        url = PUBLIC_PREFIX + 'exercises/0b5c3f9e-2a41-4c47-9f0e-1c2d3e4f5a6b.mp4'
        response = self.client.post(f'/exercises/{self.exercise.id}/video/', {
            'video': url, 'points': '13',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.exercise.refresh_from_db()
        self.assertEqual(self.exercise.video, url)

    @override_settings(**R2_SETTINGS)
    def test_exercise_video_with_r2_url_is_saved(self):
        url = R2_PUBLIC_PREFIX + 'exercises/0b5c3f9e-2a41-4c47-9f0e-1c2d3e4f5a6b.mp4'
        response = self.client.post(f'/exercises/{self.exercise.id}/video/', {
            'video': url, 'points': '13',
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.exercise.refresh_from_db()
        self.assertEqual(self.exercise.video, url)

    @override_settings(**R2_SETTINGS)
    def test_misleading_r2_prefix_is_400(self):
        response = self.client.post('/exercise_results', {
            'video': 'https://pub-test.r2.dev.evil.com/results/a.mp4',
            'training_id': 1, 'exercise_id': self.exercise.id,
        }, format='json')
        self.assertEqual(response.status_code, 400)
