"""
Settings para `python manage.py test` (manage.py lo elige solo).

Las pruebas corren sobre SQLite en memoria para no exigir Postgres local
(SQLite ignora select_for_update; eso se valida en la prueba de punta a punta).

Las variables se fijan en os.environ ANTES de importar settings, porque
settings.py las lee al importarse y read_env() no sobrescribe las ya definidas:
así un .env de desarrollo nunca apunta las pruebas a una base o servicio real.
Los hosts de integración apuntan a dominios .test (reservados, no resuelven):
si una prueba olvidara simular `requests`, falla en vez de llamar al servicio real.
"""
import os

os.environ['DATABASE_URL'] = 'sqlite://:memory:'
os.environ.setdefault('SECRET_KEY', 'test-only-secret-key')
os.environ['PLATFORM_PUBLIC_URL'] = 'https://platform.test'
os.environ['PROCESSING_SERVICE_URL'] = 'https://processing.test'
os.environ['PROCESSING_HMAC_SECRET'] = 'test-hmac-secret'
os.environ['SUPABASE_URL'] = 'https://supabase.test'
os.environ['SUPABASE_SECRET_KEY'] = 'test-supabase-secret'
os.environ['SUPABASE_BUCKET'] = 'videos'
# Como hoy en producción: Supabase activo y R2 sin configurar. Las pruebas de R2
# lo configuran con override_settings y siempre simulan el cliente S3.
os.environ['STORAGE_BACKEND'] = 'supabase'
for _name in ('R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET', 'R2_PUBLIC_BASE_URL'):
    os.environ[_name] = ''

from .settings import *  # noqa: E402,F401,F403

# Las pruebas provocan errores a propósito; los que importan se comprueban con
# assertLogs, que ignora este nivel.
LOGGING['loggers']['telerehabilitation_API']['level'] = 'CRITICAL'  # noqa: F405
