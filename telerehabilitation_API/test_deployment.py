import io
import os
import re
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.db.utils import OperationalError
import yaml
from django.test import SimpleTestCase, TestCase

import create_superuser  # script de la raíz del repo (entrypoint.sh)
from telerehabilitation_API.authentication.models import Admin

BASE_DIR = Path(__file__).resolve().parent.parent

INTEGRATION_VARS = (
    'PLATFORM_PUBLIC_URL', 'RENDER_EXTERNAL_URL', 'RENDER_EXTERNAL_HOSTNAME',
    'PROCESSING_SERVICE_URL', 'PROCESSING_HMAC_SECRET',
    'SUPABASE_URL', 'SUPABASE_SECRET_KEY', 'SUPABASE_BUCKET',
    'STORAGE_BACKEND', 'R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET',
    'R2_PUBLIC_BASE_URL',
)
R2_KEYS = ('STORAGE_BACKEND', 'R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY',
           'R2_BUCKET', 'R2_PUBLIC_BASE_URL')


def run_with_production_settings(code, **extra_env):
    """
    Corre `code` en un proceso nuevo con los settings de producción (no los de
    pruebas), sin variables de integración. Hace falta un proceso aparte porque
    settings.py se evalúa una sola vez al importarse.
    """
    env = {k: v for k, v in os.environ.items() if k not in INTEGRATION_VARS}
    env.update(
        DJANGO_SETTINGS_MODULE='telerehabilitation_API.settings',
        SECRET_KEY='startup-test',
        DATABASE_URL='sqlite://:memory:',
        **extra_env,
    )
    preamble = 'import django; django.setup()\n'
    return subprocess.run(
        [sys.executable, '-c', preamble + code],
        cwd=BASE_DIR, env=env, capture_output=True, text=True, timeout=120,
    )


class StartupWithoutIntegrationTests(SimpleTestCase):
    def test_boots_without_supabase_or_processing_vars(self):
        result = run_with_production_settings(
            'import telerehabilitation_API.urls\n'
            'from django.core.management import call_command\n'
            'call_command("check")\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_storage_backend_defaults_to_supabase(self):
        result = run_with_production_settings('from django.conf import settings; print(settings.STORAGE_BACKEND)')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'supabase')

    def test_r2_backend_boots_without_r2_vars(self):
        # Sin las variables de R2 solo falla la subida (503), no el arranque.
        result = run_with_production_settings(
            'from django.core.management import call_command; call_command("check")',
            STORAGE_BACKEND='R2',
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_storage_backend_stops_startup(self):
        result = run_with_production_settings('pass', STORAGE_BACKEND='s3')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('STORAGE_BACKEND', result.stderr)

    def test_platform_public_url_falls_back_to_render_external_url(self):
        result = run_with_production_settings(
            'from django.conf import settings; print(settings.PLATFORM_PUBLIC_URL)',
            RENDER_EXTERNAL_URL='https://platform-api-ab12.onrender.com/',
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'https://platform-api-ab12.onrender.com')

    def test_explicit_platform_public_url_wins(self):
        result = run_with_production_settings(
            'from django.conf import settings; print(settings.PLATFORM_PUBLIC_URL)',
            RENDER_EXTERNAL_URL='https://platform-api-ab12.onrender.com',
            PLATFORM_PUBLIC_URL='https://api.example.org',
        )
        self.assertEqual(result.stdout.strip(), 'https://api.example.org')


class HealthTests(TestCase):
    def test_health_ok_without_auth(self):
        response = self.client.get('/health')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_health_accepts_render_internal_host(self):
        # El health check de Render llega con este Host; no debe dar 400.
        response = self.client.get('/health', HTTP_HOST='localhost:10000')
        self.assertEqual(response.status_code, 200)

    def test_health_reports_database_down(self):
        with mock.patch(
            'telerehabilitation_API.health.connection.ensure_connection',
            side_effect=OperationalError('down'),
        ):
            response = self.client.get('/health')
        self.assertEqual(response.status_code, 503)


class SuperuserScriptTests(TestCase):
    """create_superuser.py: superusuario con rol de administrador en el panel."""

    def setUp(self):
        # El grupo "Admin" lo crea el fixture, igual que en entrypoint.sh.
        call_command('loaddata', BASE_DIR / 'initial_data.json', verbosity=0)

    def ensure(self, username='root', email='root@example.com', password='a-strong-pass-123'):
        with redirect_stdout(io.StringIO()):
            return create_superuser.ensure_superuser(username, email, password)

    def assert_is_platform_admin(self, user):
        self.assertTrue(user.is_superuser)
        self.assertTrue(user.groups.filter(name='Admin').exists())
        self.assertEqual(Admin.objects.filter(user=user).count(), 1)

    def test_new_superuser_gets_admin_group_and_row(self):
        user = self.ensure()
        self.assert_is_platform_admin(user)
        self.assertTrue(user.check_password('a-strong-pass-123'))

    def test_is_idempotent(self):
        self.ensure()
        user = self.ensure()
        self.assertEqual(get_user_model().objects.filter(username='root').count(), 1)
        self.assertEqual(user.groups.filter(name='Admin').count(), 1)
        self.assert_is_platform_admin(user)

    def test_completes_superuser_created_before_the_fix(self):
        # Estado del despliegue de Render: superusuario sin grupo ni fila Admin.
        existing = get_user_model().objects.create_superuser('root', 'root@example.com', 'old-pass-456')
        user = self.ensure(password='otra-clave-789')
        self.assertEqual(user.pk, existing.pk)
        self.assert_is_platform_admin(user)
        user.refresh_from_db()
        self.assertTrue(user.check_password('old-pass-456'))  # nunca cambia la contraseña

    def test_regular_user_with_same_name_is_not_promoted(self):
        get_user_model().objects.create_user('root', password='x-pass-123')
        user = self.ensure()
        self.assertFalse(user.is_superuser)
        self.assertFalse(user.groups.exists())
        self.assertFalse(Admin.objects.exists())

    def test_missing_admin_group_does_not_crash(self):
        Group.objects.filter(name='Admin').delete()
        user = self.ensure()
        self.assertTrue(user.is_superuser)
        self.assertEqual(Admin.objects.filter(user=user).count(), 1)

    def test_main_does_nothing_without_variables(self):
        clean_env = {k: v for k, v in os.environ.items() if not k.startswith('DJANGO_SUPERUSER_')}
        with mock.patch.dict(os.environ, clean_env, clear=True), redirect_stdout(io.StringIO()):
            create_superuser.main()
        self.assertFalse(get_user_model().objects.exists())

    def test_main_reads_variables(self):
        variables = {
            'DJANGO_SUPERUSER_USERNAME': 'root',
            'DJANGO_SUPERUSER_EMAIL': 'root@example.com',
            'DJANGO_SUPERUSER_PASSWORD': 'a-strong-pass-123',
        }
        with mock.patch.dict(os.environ, variables), redirect_stdout(io.StringIO()):
            create_superuser.main()
        self.assert_is_platform_admin(get_user_model().objects.get(username='root'))


class AdminAndDocsTests(TestCase):
    def test_swagger_renders(self):
        # Solo para administradores (docs_access.py); sin sesión es 404.
        self.client.force_login(get_user_model().objects.create_superuser('root', 'root@example.com', 'a-strong-pass-123'))
        response = self.client.get('/swagger/')
        self.assertEqual(response.status_code, 200)

    def test_superuser_can_open_admin(self):
        get_user_model().objects.create_superuser('root', 'root@example.com', 'a-strong-pass-123')
        self.client.login(username='root', password='a-strong-pass-123')
        response = self.client.get('/admin/')
        self.assertEqual(response.status_code, 200)

    def test_unknown_origin_gets_no_cors_header(self):
        response = self.client.get('/health', HTTP_ORIGIN='https://evil.example')
        self.assertNotIn('Access-Control-Allow-Origin', response)


class BlueprintTests(SimpleTestCase):
    """render.yaml y entrypoint.sh esperan lo mismo al detener un worker."""

    def test_render_waits_as_long_as_gunicorn_on_shutdown(self):
        blueprint = yaml.safe_load((BASE_DIR / 'render.yaml').read_text(encoding='utf-8'))
        api = next(s for s in blueprint['services'] if s['name'] == 'platform-api')
        delay = api['maxShutdownDelaySeconds']
        # Rango del blueprint de Render; no se puede fijar en un servicio con disco.
        self.assertIsInstance(delay, int)
        self.assertTrue(1 <= delay <= 300)
        self.assertNotIn('disk', api)

        entrypoint = (BASE_DIR / 'entrypoint.sh').read_text(encoding='utf-8')
        graceful = int(re.search(r'--graceful-timeout (\d+)', entrypoint).group(1))
        self.assertEqual(delay, graceful)

    def test_r2_keys_are_declared_without_values(self):
        blueprint = yaml.safe_load((BASE_DIR / 'render.yaml').read_text(encoding='utf-8'))
        api = next(s for s in blueprint['services'] if s['name'] == 'platform-api')
        env_vars = {v['key']: v for v in api['envVars']}
        for key in R2_KEYS:
            with self.subTest(key=key):
                self.assertEqual(env_vars[key], {'key': key, 'sync': False})
