"""
Supabase Storage: URLs firmadas de subida (docs/DECISIONES_TECNICAS.md, fase 3). La
validación de las URLs de video que recibe la API está en storage/backend.py,
porque acepta las de Supabase y las de R2.

Se habla con la API REST de Storage usando `requests`, sin el SDK de Supabase:
son dos llamadas y `requests` ya es dependencia.

Flujo: el backend pide a Supabase una URL firmada para una ruta que él mismo
elige; el navegador sube el archivo directo a esa URL con un PUT (sin ninguna
clave: el token de la URL es la única credencial, verificado con el proyecto
real el 2026-10-15) y luego le pasa al backend la URL pública.

Lo verificado contra la documentación y el código de Storage (2026-10-15):
  - POST /storage/v1/object/upload/sign/{bucket}/{ruta} devuelve
    {"url": "/object/upload/sign/...?token=...", "token": "..."}: la url es
    RELATIVA a /storage/v1.
  - El token queda atado a ese bucket y esa ruta, y sin upsert: no sirve para
    subir a otra ruta ni para sobrescribir un archivo existente.
  - Supabase aplica en el PUT el límite de tamaño y los tipos del bucket, con
    el Content-Type del PUT. Por eso el panel debe mandar el mismo
    content_type que validó este backend.
"""
import os
import uuid
from urllib.parse import quote

import requests
from django.conf import settings

# Igual a los tipos permitidos del bucket y a los formatos que acepta el
# Servicio de Procesamiento (mp4, mov, webm, mkv, avi).
ALLOWED_VIDEO_TYPES = {
    'video/mp4': '.mp4',
    'video/quicktime': '.mov',
    'video/webm': '.webm',
    'video/x-matroska': '.mkv',
    'video/x-msvideo': '.avi',
}

UPLOAD_FOLDERS = {
    'exercise': 'exercises',
    'result': 'results',
}

REQUIRED_SETTINGS = ('SUPABASE_URL', 'SUPABASE_SECRET_KEY', 'SUPABASE_BUCKET')

# Conectar debería ser inmediato; firmar una URL no sube ningún archivo.
TIMEOUT_SECONDS = 10


class StorageError(Exception):
    """Supabase no pudo emitir la URL firmada."""


def missing_settings():
    """Nombres de las variables de Supabase que faltan (lista vacía si ninguna)."""
    return [name for name in REQUIRED_SETTINGS if not getattr(settings, name)]


def _headers():
    """
    Las claves nuevas (sb_secret_...) no son JWT y van solo en `apikey`: el
    gateway de Supabase las traduce a un JWT para Storage. Las heredadas
    (service_role, un JWT "eyJ...") van además como Bearer. Mismo criterio que
    app/platforms/render/supabase_storage.py del Servicio de Procesamiento.
    """
    key = settings.SUPABASE_SECRET_KEY
    headers = {'apikey': key}
    if key.startswith('eyJ'):
        headers['Authorization'] = f'Bearer {key}'
    return headers


def build_object_path(kind, content_type, filename=''):
    """
    Ruta del objeto dentro del bucket: `<carpeta>/<uuid><ext>`.

    Del nombre original solo se conserva la extensión: el nombre puede
    identificar al paciente (Ley 1581). Si la extensión no es de video, se usa
    la que corresponde al content_type, porque el Servicio de Procesamiento
    decide el formato por la extensión.
    """
    extension = os.path.splitext(filename or '')[1].lower()
    if extension not in ALLOWED_VIDEO_TYPES.values():
        extension = ALLOWED_VIDEO_TYPES[content_type]
    return f'{UPLOAD_FOLDERS[kind]}/{uuid.uuid4()}{extension}'


def public_url_prefix():
    return f'{settings.SUPABASE_URL}/storage/v1/object/public/{settings.SUPABASE_BUCKET}/'


def public_url(path):
    return public_url_prefix() + path


def create_signed_upload_url(path, content_type=None):
    """
    Pide a Supabase la URL firmada para subir a `path` y la devuelve absoluta.

    `content_type` no se firma: Supabase aplica los tipos del bucket con el
    Content-Type del PUT. Se recibe para tener la misma interfaz que storage/r2.py.
    """
    bucket = quote(settings.SUPABASE_BUCKET, safe='')
    endpoint = (
        f'{settings.SUPABASE_URL}/storage/v1/object/upload/sign/'
        f'{bucket}/{quote(path, safe="/")}'
    )
    try:
        response = requests.post(endpoint, headers=_headers(), json={}, timeout=TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        raise StorageError(f'Sin respuesta de Supabase Storage: {exc.__class__.__name__}') from exc

    if not response.ok:
        # El cuerpo trae el motivo ("Bucket not found"...); la clave va en los
        # encabezados de la solicitud, nunca en la respuesta.
        raise StorageError(f'Supabase Storage respondió HTTP {response.status_code}: {response.text[:300]}')

    try:
        relative_url = response.json()['url']
    except (ValueError, KeyError, TypeError) as exc:
        raise StorageError('La respuesta de Supabase Storage no trae "url".') from exc
    if not isinstance(relative_url, str) or not relative_url.startswith('/'):
        raise StorageError('La "url" de Supabase Storage no es una ruta relativa.')

    return f'{settings.SUPABASE_URL}/storage/v1{relative_url}'

