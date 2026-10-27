"""
Callbacks firmados del Servicio de Procesamiento (fase 5; v2 en la fase V2).

Un único manejador para los dos endpoints (paciente y referencia). Reglas:

  - La firma se verifica sobre el cuerpo crudo, leído ANTES que
    `request.data` (al revés Django lanza RawPostDataException).
  - Un dato raro nunca produce un 500: el servicio reintenta los 5xx durante
    72 h, así que un 500 por un dato malformado se repetiría cientos de veces.
    401 firma inválida, 404 recurso inexistente, 400 cuerpo ilegible,
    413 cuerpo de más de 25 MB.
  - Idempotente: un callback duplicado deja exactamente el mismo estado.

Callback v2 (`contract_version: 2`): `results.joints` (una entrada por terna)
y `results.frames` (por frame: `t`, 33 `landmarks`, 33 `world_landmarks`,
`angles_2d` y `angles_3d` en el orden de `joints`). Cada terna se mapea a su
articulación por la terna completa y se arma el movimiento (decisión D4).

Callback v1 (con `?point_tracked=`): se sigue aceptando para el PACIENTE
durante la transición, para los jobs que se enviaron con el backend v1 y cuyos
avisos (o reintentos, hasta 72 h) llegan después del despliegue de v2. Para la
REFERENCIA se rechaza (404): sus articulaciones se conservan al cambiar el
video y el aviso v1 no trae la huella del video que lo distinga de uno viejo.
"""
import json
import math

from django.conf import settings
from rest_framework import status
from rest_framework.response import Response

from telerehabilitation_API.processing.hmac_auth import is_valid_signature
from telerehabilitation_API.processing.results import (
    REFERENCE, Target, TargetNotFound, record_job_outcome, record_joint_outcome,
)

INVALID_ANGLES_ERROR = 'El servicio devolvió ángulos con un formato inválido.'

# El callback v2 trae los 33 puntos por frame: ~6 MB para 2 minutos a 30 fps.
# Solo estos dos endpoints admiten un cuerpo así; el resto de la API conserva
# el límite por defecto de Django (2,5 MB).
CALLBACK_MAX_BYTES = 25 * 1024 * 1024

LANDMARKS = 33


class UnreadablePayload(Exception):
    """Cuerpo que no se puede interpretar como callback (400)."""


class BodyTooLarge(Exception):
    """Cuerpo por encima de CALLBACK_MAX_BYTES (413)."""


def handle_callback(request, kind, target_id):
    """Procesa el callback de `Target(kind, target_id)`."""
    try:
        raw_body = read_body(request, CALLBACK_MAX_BYTES)
    except BodyTooLarge:
        return Response({'error': 'Cuerpo demasiado grande.'}, status=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE)

    # Lo que firmó el servicio, byte a byte.
    if not is_valid_signature(
        request.headers.get('X-Timestamp'),
        request.headers.get('X-Signature'),
        raw_body,
        settings.PROCESSING_HMAC_SECRET,
    ):
        return Response({'error': 'Firma inválida.'}, status=status.HTTP_401_UNAUTHORIZED)

    target = _as_int(target_id)
    if target is None:
        return Response({}, status=status.HTTP_404_NOT_FOUND)

    try:
        payload = load_payload(raw_body)
        if payload.get('contract_version') == 2:
            outcome = parse_payload_v2(payload)
            # Los frames saneados ya tienen lo necesario: soltar el cuerpo
            # decodificado (~20 MB para 60 s a 30 fps) antes de armar y
            # guardar el movimiento baja el pico del callback.
            payload = raw_body = None
            record_job_outcome(Target(kind, target), video_digest_param=request.GET.get('video'), **outcome)
        elif kind == REFERENCE:
            # Las articulaciones de referencia ahora se conservan al cambiar el
            # video (therapy/tracked_points.py), y el aviso v1 no trae la huella
            # del video: uno viejo escribiría ángulos de otro video en la misma
            # fila. Solo podía venir de un job enviado antes de la v2.
            raise TargetNotFound()
        else:
            point_tracked_id = _as_int(request.GET.get('point_tracked'))
            if point_tracked_id is None:
                return Response({'error': 'Callback v1 sin point_tracked.'}, status=status.HTTP_400_BAD_REQUEST)
            record_joint_outcome(Target(kind, target), point_tracked_id, **parse_payload(payload))
    except UnreadablePayload as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
    except TargetNotFound:
        # También cubre avisos viejos de un video de referencia reemplazado
        # (huella del video distinta) y los avisos v1 de referencia.
        return Response({}, status=status.HTTP_404_NOT_FOUND)
    return Response({}, status=status.HTTP_200_OK)


def read_body(request, limit):
    """
    Cuerpo crudo con un tope propio.

    No se usa `request.body` porque Django lo limita con
    DATA_UPLOAD_MAX_MEMORY_SIZE (2,5 MB), que es global: subirlo para los
    callbacks lo subiría para toda la API. Se lee del stream con el tope de
    estos dos endpoints.
    """
    django_request = getattr(request, '_request', request)
    declared = django_request.META.get('CONTENT_LENGTH') or '0'
    if declared.isdigit() and int(declared) > limit:
        raise BodyTooLarge()
    body = django_request.read(limit + 1)
    if len(body) > limit:
        raise BodyTooLarge()
    return body


def load_payload(raw_body):
    try:
        # NaN/Infinity son JSON válido para Python pero no para Postgres
        # (jsonb): se leen como null.
        payload = json.loads(raw_body, parse_constant=lambda _constant: None)
    except (ValueError, UnicodeDecodeError) as exc:
        raise UnreadablePayload('El cuerpo no es JSON válido.') from exc
    if not isinstance(payload, dict):
        raise UnreadablePayload('El cuerpo debe ser un objeto JSON.')
    return payload


def parse_payload_v2(payload):
    """
    Traduce el callback v2 a los argumentos de record_job_outcome, saneando lo
    que llega: una entrada o un frame malformados se omiten en vez de rechazar
    el aviso (un 400 no se reintenta y dejaría el resultado sin cerrar). Una
    articulación cuya entrada se omitió queda en error ("no devuelta").
    """
    error = _as_error(payload.get('error'))
    results = payload.get('results') if isinstance(payload.get('results'), dict) else {}

    joints = []
    for index, joint in enumerate(results.get('joints') or []):
        triplet = _as_triplet(joint.get('points')) if isinstance(joint, dict) else None
        if triplet is None:
            continue
        max_angle, max_ok = _as_number(joint.get('max_angle'))
        min_angle, min_ok = _as_number(joint.get('min_angle'))
        joints.append({
            'triplet': triplet,
            'index': index,
            'max_angle': max_angle if max_ok else None,
            'min_angle': min_angle if min_ok else None,
        })

    frames = [frame for frame in map(_as_frame, results.get('frames') or []) if frame is not None]
    duration_s, _ok = _as_number(results.get('duration_s'))
    return {'error': error, 'joints': joints, 'frames': frames, 'duration_s': duration_s}


def parse_payload(payload):
    """
    Callback v1: traduce el cuerpo a los argumentos de record_joint_outcome.

    Un ángulo con un tipo raro no rechaza el callback (400): un rechazo deja la
    articulación "procesando" para siempre, porque el servicio no reintenta los
    4xx. Se registra como error de la articulación, que sí cierra el resultado.
    """
    error = _as_error(payload.get('error'))
    max_angle, max_ok = _as_number(payload.get('max_angle'))
    min_angle, min_ok = _as_number(payload.get('min_angle'))
    if error is None and not (max_ok and min_ok):
        error = INVALID_ANGLES_ERROR

    return {
        'max_angle': max_angle,
        'min_angle': min_angle,
        'angle_series': _angle_series_v1(payload.get('results')),
        'error': error,
    }


def _angle_series_v1(results):
    """[{t_ms, angle}] por frame; los frames malformados se omiten."""
    frames = results.get('points') if isinstance(results, dict) else None
    if not isinstance(frames, list):
        return None
    series = []
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        t_ms, t_ok = _as_number(frame.get('timestamp_ms'))
        angle, angle_ok = _as_number(frame.get('angle'))
        if t_ms is None or not t_ok:
            continue
        series.append({'t_ms': t_ms, 'angle': angle if angle_ok else None})
    return series


def _as_frame(frame):
    """Frame v2 saneado, o None si no se puede usar (sin `t` numérico)."""
    if not isinstance(frame, dict):
        return None
    t, t_ok = _as_number(frame.get('t'))
    if t is None or not t_ok:
        return None
    landmarks = _as_points(frame.get('landmarks'), 4)
    return {
        't': t,
        'landmarks': landmarks,
        # Sin 2D no hay 3D útil para el panel: el movimiento omite ese frame.
        'world_landmarks': _as_points(frame.get('world_landmarks'), 3) if landmarks else None,
        'angles_2d': _as_angles(frame.get('angles_2d')),
        'angles_3d': _as_angles(frame.get('angles_3d')),
    }


def _as_points(value, size):
    """33 puntos de `size` números, o None si la forma no es esa."""
    if not isinstance(value, list) or len(value) != LANDMARKS:
        return None
    points = []
    for point in value:
        if not isinstance(point, list) or len(point) != size:
            return None
        numbers = [_as_number(coordinate) for coordinate in point]
        if any(number is None or not ok for number, ok in numbers):
            return None
        points.append([number for number, _ok in numbers])
    return points


def _as_angles(value):
    if not isinstance(value, list):
        return []
    angles = []
    for item in value:
        angle, ok = _as_number(item)
        angles.append(angle if ok else None)
    return angles


def _as_triplet(value):
    if (
        isinstance(value, list) and len(value) == 3
        and all(isinstance(p, int) and not isinstance(p, bool) for p in value)
    ):
        return tuple(value)
    return None


def _as_error(error):
    if error is not None and not isinstance(error, str):
        error = json.dumps(error, ensure_ascii=False)
    return error or None


def _as_number(value):
    """(número finito o None, ¿el valor era válido?). null es válido."""
    if value is None:
        return None, True
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None, False
    return float(value), True


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
