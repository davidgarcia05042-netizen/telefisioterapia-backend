"""
Envío de videos al Servicio de Procesamiento (docs/DECISIONES_TECNICAS.md, fase V2).

Un solo job por video (decisión D1): `points` lleva la lista de ternas
[vecino1, vértice, vecino2] de todas las articulaciones rastreadas, y el
servicio corre MediaPipe una vez para todas (contrato v2). En v1 era un job
por articulación y el mismo video se procesaba N veces.

El envío corre en un hilo que se lanza después del commit de la vista: la
vista responde de inmediato y el resultado llega después por callback.

El hilo NO es daemon: cuando gunicorn recicla un worker (--max-requests) o lo
detiene con SIGTERM (--graceful-timeout 90, ver entrypoint.sh), Python espera
a que el envío en curso termine antes de salir. Con un hilo daemon el envío se
cortaba a mitad de la subida, el servicio nunca recibía el job y el resultado
quedaba en "procesando" para siempre.

Limitación conocida: sin cola de trabajos, si el contenedor se corta de golpe
(Render lo mata por memoria o antes de que termine la espera), ese resultado
queda en "procesando".

Memoria: el video nunca se carga entero. Se descarga a un temporal, el
multipart se escribe en otro temporal, se firma leyéndolo por bloques y se
envía en streaming. Antes requests armaba el multipart en memoria y la firma
lo volvía a concatenar: dos copias del video por envío, y glibc dejaba
retenida más o menos una en el worker. En M5 (2026-10-19) tres envíos
seguidos llevaron el backend a más de 512 MB y Render lo reinició.
"""
import json
import logging
import os
import shutil
import tempfile
import threading
import time
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.db import connection, transaction
from urllib3.fields import RequestField
from urllib3.filepost import choose_boundary

from telerehabilitation_API.processing.hmac_auth import signed_file_headers
from telerehabilitation_API.processing.results import (  # noqa: F401 (resolve_points se reexporta)
    MISSING_NEIGHBORS_ERROR, PATIENT, TargetNotFound, mark_without_joints, record_joint_outcome,
    resolve_points, video_digest,
)
from telerehabilitation_API.storage.supabase import ALLOWED_VIDEO_TYPES
from telerehabilitation_API.therapy.models import Exercise, ExerciseResult, ExerciseSkeletonPointTracked

logger = logging.getLogger(__name__)

REQUIRED_SETTINGS = ('PROCESSING_SERVICE_URL', 'PROCESSING_HMAC_SECRET', 'PLATFORM_PUBLIC_URL')

# Igual a MAX_VIDEO_SIZE_MB del servicio y al límite del bucket.
MAX_VIDEO_BYTES = 50 * 1024 * 1024
DOWNLOAD_CHUNK_BYTES = 1024 * 1024
# Bloque al copiar el video dentro del multipart.
COPY_CHUNK_BYTES = 1024 * 1024
# (conexión, lectura entre bloques): el total depende del tamaño del video.
DOWNLOAD_TIMEOUT = (10, 60)
SEND_TIMEOUT = 120
# El servicio admite 10 solicitudes por minuto; ante un 429 se espera lo que
# indique Retry-After (con tope) y se reintenta una sola vez.
MAX_RETRY_AFTER_SECONDS = 60

CONTENT_TYPE_BY_EXTENSION = {ext: content_type for content_type, ext in ALLOWED_VIDEO_TYPES.items()}


class DownloadError(Exception):
    """No se pudo obtener el video; el mensaje queda como error de cada articulación."""


def missing_settings():
    """Nombres de las variables del servicio que faltan (lista vacía si ninguna)."""
    return [name for name in REQUIRED_SETTINGS if not getattr(settings, name)]


def results_endpoint(target, video_url):
    """
    URL del callback del job. La de referencia lleva barra final porque es una
    acción del router de DRF; la del paciente no (path explícito).

    Sin `point_tracked` (v2: un aviso trae todas las articulaciones). Lleva la
    huella del video (`video`): permite rechazar el aviso viejo de un video de
    referencia reemplazado, que en v1 se detectaba por `point_tracked`.
    """
    base = settings.PLATFORM_PUBLIC_URL
    digest = video_digest(video_url)
    if target.kind == PATIENT:
        return f'{base}/exercise_results/{target.id}/results?video={digest}'
    return f'{base}/exercises/{target.id}/video_results/?video={digest}'


def dispatch_after_commit(target, video_url):
    """
    Programa el envío en un hilo cuando la transacción de la vista haga
    commit: así el hilo nunca lee un resultado o unas articulaciones que aún no
    son visibles (o que se revirtieron). No es daemon: el worker que se va
    espera a que termine (ver el docstring del módulo).
    """
    def start():
        threading.Thread(
            target=_dispatch_in_thread, args=(target, video_url),
            name=f'dispatch-{target.kind}-{target.id}', daemon=False,
        ).start()

    transaction.on_commit(start)


def _dispatch_in_thread(target, video_url):
    try:
        dispatch(target, video_url)
    except Exception:
        logger.exception('Falló el envío de %s %s al servicio', target.kind, target.id)
    finally:
        # Cada hilo abre su propia conexión a la base; si no se cierra queda
        # abierta hasta que Postgres la corte.
        connection.close()


def job_triplets(joints):
    """
    Ternas únicas del job, en el orden de los `id` de las articulaciones.

    Devuelve (ternas, articulaciones enviables). Una articulación sin terna
    válida no entra al job; dos articulaciones con la misma terna la comparten
    (se envía una vez y el callback la asigna a ambas).
    """
    triplets, sendable = [], []
    for tracked in joints:
        points = resolve_points(tracked)
        if points is None:
            continue
        sendable.append(tracked)
        if points not in triplets:
            triplets.append(points)
    return triplets, sendable


def dispatch(target, video_url):
    """Descarga el video una vez y envía UN job con todas las ternas."""
    context = _job_context(target)
    if context is None:
        logger.warning('%s %s ya no existe: no se envía', target.kind, target.id)
        return
    exercise_id, joints = context
    if not joints:
        mark_without_joints(target)
        return

    triplets, sendable = job_triplets(joints)
    for tracked in joints:
        if tracked not in sendable:
            _record_error(target, tracked.id, MISSING_NEIGHBORS_ERROR)
    if not sendable:
        return

    missing = missing_settings()
    if missing:
        _record_errors(target, sendable, f'Servicio de procesamiento no configurado: falta {", ".join(missing)}.')
        return

    try:
        video_path, content_type = _download(video_url)
    except DownloadError as exc:
        _record_errors(target, sendable, str(exc))
        return

    try:
        try:
            error = _send_job(target, exercise_id, triplets, video_url, video_path, content_type)
        except Exception:
            logger.exception('Error inesperado enviando el video de %s %s', target.kind, target.id)
            error = 'Error inesperado al enviar el video al servicio de procesamiento.'
        if error:
            # Un solo job: si no salió, ninguna articulación recibirá callback.
            _record_errors(target, sendable, error)
    finally:
        os.remove(video_path)


def _job_context(target):
    """(exercise_id, articulaciones rastreadas) del video, o None si el destino no existe."""
    if target.kind == PATIENT:
        exercise_id = ExerciseResult.objects.filter(pk=target.id).values_list('exercise_id', flat=True).first()
    else:
        exercise_id = Exercise.objects.filter(pk=target.id).values_list('id', flat=True).first()
    if exercise_id is None:
        return None
    joints = list(
        ExerciseSkeletonPointTracked.objects.filter(exercise_id=exercise_id)
        .select_related('skeleton_point').order_by('id')
    )
    return exercise_id, joints


def _record_error(target, point_tracked_id, error):
    try:
        record_joint_outcome(target, point_tracked_id, error=error)
    except TargetNotFound:
        # Se borró el resultado o se reemplazó el video de referencia mientras
        # tanto: no hay dónde registrar el error.
        logger.warning('No se registró el error de la articulación %s: ya no existe', point_tracked_id)


def _record_errors(target, joints, error):
    for tracked in joints:
        _record_error(target, tracked.id, error)


def _download(video_url):
    """
    Descarga el video a un temporal en streaming, con tope de 50 MB.

    La URL ya pasó el validador anti-SSRF (bucket propio); además no se siguen
    redirecciones, para que una respuesta 3xx no lleve la descarga a otro host.
    """
    extension = os.path.splitext(urlsplit(video_url).path)[1].lower() or '.mp4'
    fd, path = tempfile.mkstemp(suffix=extension)
    try:
        with os.fdopen(fd, 'wb') as file, requests.get(
            video_url, stream=True, timeout=DOWNLOAD_TIMEOUT, allow_redirects=False,
        ) as response:
            if response.status_code != 200:
                raise DownloadError(f'No se pudo descargar el video (HTTP {response.status_code}).')
            declared = response.headers.get('Content-Length', '')
            if declared.isdigit() and int(declared) > MAX_VIDEO_BYTES:
                raise DownloadError('El video supera el límite de 50 MB.')

            size = 0
            for chunk in response.iter_content(chunk_size=DOWNLOAD_CHUNK_BYTES):
                size += len(chunk)
                if size > MAX_VIDEO_BYTES:
                    raise DownloadError('El video supera el límite de 50 MB.')
                file.write(chunk)
            if size == 0:
                raise DownloadError('El video descargado está vacío.')

            header_type = response.headers.get('Content-Type', 'application/octet-stream')
            content_type = CONTENT_TYPE_BY_EXTENSION.get(extension) or header_type.split(';')[0].strip()
    except requests.RequestException as exc:
        os.remove(path)
        raise DownloadError(f'No se pudo descargar el video ({exc.__class__.__name__}).') from exc
    except BaseException:
        os.remove(path)
        raise
    return path, content_type


def _send_job(target, exercise_id, triplets, video_url, video_path, content_type):
    """
    Envía el job. Devuelve None si el servicio lo aceptó o el motivo del error.

    El servicio responde HTTP 200 también cuando rechaza el video: el éxito se
    confirma solo con status == "ok" y un job_id en el JSON.

    El multipart se escribe una vez en un temporal y sirve también para el
    reintento ante un 429 (con firma nueva: el timestamp cambia).
    """
    fd, body_path = tempfile.mkstemp(suffix='.multipart')
    os.close(fd)
    try:
        body_type = write_multipart(
            body_path,
            {
                'points': json.dumps(triplets),
                'exercise': f'exercise:{exercise_id}',
                'resultsEndpoint': results_endpoint(target, video_url),
            },
            video_path, f'video{os.path.splitext(video_path)[1]}', content_type,
        )
        response = _post_video(target, body_path, body_type)
        if response.status_code == 429:
            time.sleep(_retry_after_seconds(response))
            response = _post_video(target, body_path, body_type)
    finally:
        os.remove(body_path)

    if response.status_code == 0:
        return 'No se pudo contactar al servicio de procesamiento.'
    if response.status_code != 200:
        return f'El servicio de procesamiento respondió HTTP {response.status_code}.'
    try:
        body = response.json()
    except ValueError:
        return 'Respuesta ilegible del servicio de procesamiento.'
    if not isinstance(body, dict) or body.get('status') != 'ok' or not body.get('job_id'):
        code = body.get('internal_status_code') if isinstance(body, dict) else None
        return f'El servicio de procesamiento rechazó el video ({code or "sin motivo"}).'

    logger.info('Job %s creado: %s %s, ternas %s', body['job_id'], target.kind, target.id, triplets)
    return None


def write_multipart(path, fields, video_path, filename, content_type, boundary=None):
    """
    Escribe en `path` el cuerpo multipart/form-data del job y devuelve su
    Content-Type (con el boundary).

    Mismos bytes que armaba requests con `data=fields` y `files={'video': ...}`:
    los campos primero, en orden, y el video al final, con los encabezados que
    genera urllib3 (RequestField). Lo único distinto es que el video se copia
    por bloques en vez de cargarse entero.

    `points` va SIEMPRE en la forma v2 (lista de ternas), aunque sea una sola:
    así el callback siempre trae `joints` y `frames` con los 33 puntos.
    """
    boundary = boundary or choose_boundary()
    with open(path, 'wb') as out:
        for name, value in fields.items():
            part = RequestField.from_tuples(name, value.encode('utf-8'))
            _write_part_header(out, boundary, part)
            out.write(part.data)
            out.write(b'\r\n')
        video_part = RequestField(name='video', data=b'', filename=filename)
        video_part.make_multipart(content_type=content_type)
        _write_part_header(out, boundary, video_part)
        with open(video_path, 'rb') as video:
            shutil.copyfileobj(video, out, COPY_CHUNK_BYTES)
        out.write(b'\r\n')
        out.write(f'--{boundary}--\r\n'.encode('latin-1'))
    return f'multipart/form-data; boundary={boundary}'


def _write_part_header(out, boundary, part):
    out.write(f'--{boundary}\r\n'.encode('latin-1'))
    out.write(part.render_headers().encode('utf-8'))


def _post_video(target, body_path, body_type):
    """
    Firma el multipart de `body_path` leyéndolo por bloques y lo envía en
    streaming: requests manda el archivo abierto, con su Content-Length. Errores
    de red se devuelven como una respuesta sintética con status_code 0 para
    tratarlos en un solo lugar.
    """
    headers = signed_file_headers(body_path, settings.PROCESSING_HMAC_SECRET)
    headers['Content-Type'] = body_type
    with open(body_path, 'rb') as body:
        prepared = requests.Request(
            'POST', f'{settings.PROCESSING_SERVICE_URL}/video/', data=body, headers=headers,
        ).prepare()
        try:
            return _send_prepared(prepared)
        except requests.RequestException as exc:
            logger.warning('Sin respuesta del servicio para %s %s: %s', target.kind, target.id, exc.__class__.__name__)
            failed = requests.Response()
            failed.status_code = 0
            return failed


def _send_prepared(prepared):
    with requests.Session() as session:
        return session.send(prepared, timeout=SEND_TIMEOUT)


def _retry_after_seconds(response):
    value = response.headers.get('Retry-After', '')
    seconds = int(value) if value.isdigit() else MAX_RETRY_AFTER_SECONDS
    return min(max(seconds, 1), MAX_RETRY_AFTER_SECONDS)
