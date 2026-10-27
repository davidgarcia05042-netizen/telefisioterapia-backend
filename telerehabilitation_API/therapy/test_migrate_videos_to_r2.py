import io
from unittest import mock

from botocore.exceptions import ClientError
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from telerehabilitation_API.therapy.management.commands import migrate_videos_to_r2 as command
from telerehabilitation_API.therapy.models import Exercise, ExerciseResult

# settings_test.py fija SUPABASE_URL=https://supabase.test y SUPABASE_BUCKET=videos.
SUPABASE = 'https://supabase.test/storage/v1/object/public/videos/'
R2_BASE = 'https://pub-test.r2.dev'
R2_SETTINGS = {
    'R2_ACCOUNT_ID': 'test-account',
    'R2_ACCESS_KEY_ID': 'test-access-key',
    'R2_SECRET_ACCESS_KEY': 'test-secret-key',
    'R2_BUCKET': 'test-bucket',
    'R2_PUBLIC_BASE_URL': R2_BASE,
}
EXERCISE_KEY = 'exercises/1c2d3e4f-0b5c-4c47-9f0e-2a413f9e5a6b.mp4'
RESULT_KEY = 'results/0b5c3f9e-2a41-4c47-9f0e-1c2d3e4f5a6b.webm'
MB = 1024 * 1024


class FakeS3:
    """R2 simulado: guarda tamaño y tipo de cada clave. No tiene delete."""

    def __init__(self, truncate=False):
        self.objects = {}
        self.uploads = []
        self.truncate = truncate

    def head_object(self, Bucket, Key):
        assert Bucket == 'test-bucket'
        if Key not in self.objects:
            raise ClientError({'Error': {'Code': '404'}}, 'HeadObject')
        return {'ContentLength': self.objects[Key]['size']}

    def upload_fileobj(self, fileobj, Bucket, Key, ExtraArgs, Config):
        # Se lee por bloques, como s3transfer: el comando le pasa un lector
        # en streaming, nunca los bytes del video.
        assert not isinstance(fileobj, (bytes, bytearray))
        size = 0
        while True:
            block = fileobj.read(Config.multipart_chunksize)
            if not block:
                break
            size += len(block)
        if self.truncate:
            size -= 1
        self.objects[Key] = {'size': size, 'content_type': ExtraArgs['ContentType']}
        self.uploads.append((Key, Config))


class FakeSupabase:
    """Respuestas de la URL pública de Supabase, por clave."""

    def __init__(self, sizes, content_type='video/mp4'):
        self.sizes = sizes
        self.content_type = content_type
        self.gets = []

    def size_of(self, url):
        return self.sizes[url[len(SUPABASE):]]

    def head(self, url, **kwargs):
        assert kwargs['allow_redirects'] is False
        return mock.Mock(status_code=200, headers={'Content-Length': str(self.size_of(url))})

    def get(self, url, **kwargs):
        assert kwargs['stream'] is True and kwargs['allow_redirects'] is False
        self.gets.append(url)
        response = mock.MagicMock(status_code=200, headers={'Content-Type': self.content_type})
        response.raw = io.BytesIO(b'\0' * self.size_of(url))
        response.__enter__.return_value = response
        return response


@override_settings(**R2_SETTINGS)
class MigrateVideosToR2Tests(TestCase):
    def setUp(self):
        self.exercise = Exercise.objects.create(
            name='Flexión de codo', description='', order=1,
            video=SUPABASE + EXERCISE_KEY, status=Exercise.PROCESSED)
        self.result = ExerciseResult.objects.create(video=SUPABASE + RESULT_KEY, status=ExerciseResult.ERROR)
        self.supabase = FakeSupabase({EXERCISE_KEY: 3 * MB, RESULT_KEY: 20 * MB + 5})
        self.s3 = FakeS3()

    def run_command(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(command.requests, 'head', side_effect=self.supabase.head), \
                mock.patch.object(command.requests, 'get', side_effect=self.supabase.get), \
                mock.patch.object(command.requests, 'delete') as delete, \
                mock.patch.object(command.r2, 'client', return_value=self.s3):
            try:
                call_command('migrate_videos_to_r2', *args, stdout=out, stderr=err)
            finally:
                delete.assert_not_called()
        return out.getvalue() + err.getvalue()

    def refresh(self):
        self.exercise.refresh_from_db()
        self.result.refresh_from_db()

    def test_dry_run_counts_and_writes_nothing(self):
        output = self.run_command('--dry-run')
        self.assertIn('2 video(s) por copiar a R2, 24.1 MB', output)
        self.assertEqual(self.s3.objects, {})
        self.assertEqual(self.supabase.gets, [])
        self.refresh()
        self.assertEqual(self.exercise.video, SUPABASE + EXERCISE_KEY)
        self.assertEqual(self.result.video, SUPABASE + RESULT_KEY)

    @override_settings(R2_SECRET_ACCESS_KEY='')
    def test_dry_run_does_not_need_r2_but_the_real_run_does(self):
        self.assertIn('por copiar a R2', self.run_command('--dry-run'))
        with self.assertRaisesMessage(CommandError, 'R2_SECRET_ACCESS_KEY'):
            self.run_command()

    def test_copies_with_same_key_and_rewrites_urls(self):
        output = self.run_command()
        self.assertEqual(self.s3.objects[EXERCISE_KEY], {'size': 3 * MB, 'content_type': 'video/mp4'})
        self.assertEqual(self.s3.objects[RESULT_KEY]['size'], 20 * MB + 5)
        self.refresh()
        self.assertEqual(self.exercise.video, f'{R2_BASE}/{EXERCISE_KEY}')
        self.assertEqual(self.result.video, f'{R2_BASE}/{RESULT_KEY}')
        self.assertIn('Copiados: 2', output)
        self.assertIn('Nada se borró de Supabase', output)

    def test_streams_in_small_blocks_without_threads(self):
        self.run_command()
        for _, config in self.s3.uploads:
            self.assertEqual(config.multipart_chunksize, 8 * MB)
            self.assertEqual(config.max_concurrency, 1)
            self.assertFalse(config.use_threads)

    def test_content_type_falls_back_to_extension(self):
        self.supabase.content_type = 'application/octet-stream'
        self.run_command()
        self.assertEqual(self.s3.objects[RESULT_KEY]['content_type'], 'video/webm')

    def test_object_already_in_r2_with_same_size_is_not_copied_again(self):
        self.s3.objects[EXERCISE_KEY] = {'size': 3 * MB, 'content_type': 'video/mp4'}
        output = self.run_command()
        self.assertEqual(self.supabase.gets, [SUPABASE + RESULT_KEY])
        self.refresh()
        self.assertEqual(self.exercise.video, f'{R2_BASE}/{EXERCISE_KEY}')
        self.assertIn('ya estaban en R2: 1', output)

    def test_object_in_r2_with_another_size_is_copied_again(self):
        self.s3.objects[EXERCISE_KEY] = {'size': 10, 'content_type': 'video/mp4'}
        self.run_command()
        self.assertEqual(self.s3.objects[EXERCISE_KEY]['size'], 3 * MB)

    def test_second_run_does_nothing(self):
        self.run_command()
        uploads = len(self.s3.uploads)
        output = self.run_command()
        self.assertEqual(len(self.s3.uploads), uploads)
        self.assertIn('no hay nada que migrar', output)

    def test_size_mismatch_keeps_the_supabase_url(self):
        self.s3 = FakeS3(truncate=True)
        with self.assertRaisesMessage(CommandError, '2 video(s) no se migraron'):
            self.run_command()
        self.refresh()
        self.assertEqual(self.exercise.video, SUPABASE + EXERCISE_KEY)
        self.assertEqual(self.result.video, SUPABASE + RESULT_KEY)

    def test_rows_being_processed_are_skipped(self):
        Exercise.objects.filter(pk=self.exercise.pk).update(status=Exercise.PROCESSING)
        output = self.run_command()
        self.refresh()
        self.assertEqual(self.exercise.video, SUPABASE + EXERCISE_KEY)
        self.assertNotIn(EXERCISE_KEY, self.s3.objects)
        self.assertEqual(self.result.video, f'{R2_BASE}/{RESULT_KEY}')
        self.assertIn('en procesamiento: 1', output)

    def test_same_video_in_two_rows_is_copied_once(self):
        twin = ExerciseResult.objects.create(video=SUPABASE + RESULT_KEY, status=ExerciseResult.PROCESSED)
        self.run_command()
        self.assertEqual([key for key, _ in self.s3.uploads].count(RESULT_KEY), 1)
        twin.refresh_from_db()
        self.assertEqual(twin.video, f'{R2_BASE}/{RESULT_KEY}')

    def test_other_urls_are_left_alone(self):
        other = ExerciseResult.objects.create(video=f'{R2_BASE}/{RESULT_KEY}', status=ExerciseResult.PROCESSED)
        empty = ExerciseResult.objects.create(video=None)
        self.run_command()
        other.refresh_from_db()
        empty.refresh_from_db()
        self.assertEqual(other.video, f'{R2_BASE}/{RESULT_KEY}')
        self.assertIsNone(empty.video)

    def test_tricky_supabase_path_is_not_copied(self):
        Exercise.objects.filter(pk=self.exercise.pk).update(video=SUPABASE + '../otro-bucket/a.mp4')
        with self.assertRaises(CommandError):
            self.run_command()
        self.assertNotIn('../otro-bucket/a.mp4', self.s3.objects)
        self.assertEqual(self.supabase.gets, [SUPABASE + RESULT_KEY])
