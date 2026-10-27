"""
Cloudflare R2: URLs firmadas de subida, con la misma interfaz que
storage/supabase.py (se elige con STORAGE_BACKEND=r2, ver storage/backend.py).

R2 habla la API de S3, así que se usa boto3: la URL de subida es un PUT
prefirmado (SigV4 en la query) que el navegador o la app usan sin ninguna
clave, igual que la URL firmada de Supabase. Firmarla es un cálculo local: no
se llama a R2 al emitirla.

Diferencias con Supabase que importan:
  - El Content-Type va FIRMADO: el PUT debe mandar exactamente el content_type
    que validó este backend (el panel y la app ya lo hacen). Con otro tipo, R2
    responde 403.
  - R2 no aplica un límite de tamaño por bucket como Supabase. El tope de
    50 MB lo siguen poniendo el panel y la app antes de subir, y el backend al
    descargar para el servicio (processing/client.py).
  - Un PUT repetido a la misma clave sobrescribe en vez de fallar; las claves
    llevan un uuid nuevo por subida, así que no choca con nada.
  - El bucket necesita CORS (PUT y GET desde el origen del panel) y acceso
    público (r2.dev o dominio propio); eso se configura en Cloudflare.
"""
from django.conf import settings

# Misma validación de tipos, mismas carpetas y misma ruta de objeto que con
# Supabase: la migración copia cada video a R2 con la misma clave.
from telerehabilitation_API.storage.supabase import (  # noqa: F401 (interfaz común)
    ALLOWED_VIDEO_TYPES, UPLOAD_FOLDERS, StorageError, build_object_path,
)

REQUIRED_SETTINGS = (
    'R2_ACCOUNT_ID', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY', 'R2_BUCKET', 'R2_PUBLIC_BASE_URL',
)

# Igual a la URL firmada de subida de Supabase, que vale 2 horas.
UPLOAD_URL_EXPIRES_SECONDS = 2 * 60 * 60


def missing_settings():
    """Nombres de las variables de R2 que faltan (lista vacía si ninguna)."""
    return [name for name in REQUIRED_SETTINGS if not getattr(settings, name)]


def endpoint_url():
    return f'https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com'


def client():
    """
    Cliente S3 apuntado a R2. boto3 se importa aquí y no arriba para que el
    backend arranque igual aunque R2 no se use.

    Las sumas de verificación automáticas de boto3 (1.36 en adelante) se
    limitan a las que exige la operación: si no, la URL prefirmada puede llevar
    parámetros x-amz-checksum-* que el navegador no sabe calcular.
    """
    import boto3
    from botocore.config import Config

    return boto3.client(
        's3',
        endpoint_url=endpoint_url(),
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name='auto',
        config=Config(
            signature_version='s3v4',
            request_checksum_calculation='when_required',
            response_checksum_validation='when_required',
        ),
    )


def public_url_prefix():
    return f'{settings.R2_PUBLIC_BASE_URL}/'


def public_url(path):
    return public_url_prefix() + path


def create_signed_upload_url(path, content_type):
    """PUT prefirmado para subir a `path` con exactamente `content_type`."""
    from botocore.exceptions import BotoCoreError, ClientError

    try:
        return client().generate_presigned_url(
            'put_object',
            Params={'Bucket': settings.R2_BUCKET, 'Key': path, 'ContentType': content_type},
            ExpiresIn=UPLOAD_URL_EXPIRES_SECONDS,
            HttpMethod='PUT',
        )
    except (BotoCoreError, ClientError) as exc:
        raise StorageError(f'No se pudo firmar la URL de R2: {exc.__class__.__name__}') from exc
