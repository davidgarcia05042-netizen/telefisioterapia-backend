"""
Ruta interna única para registrar desenlaces de articulaciones y recalcular el
estado del resultado (docs/DECISIONES_TECNICAS.md: fases 4.3, 5.3 y V2).

- `record_job_outcome`: callback v2. Un solo job trae todas las articulaciones
  del video; se registran todas bajo un mismo bloqueo, mapeadas por terna.
- `record_joint_outcome`: una articulación. La usan el envío (cuando un job no
  se pudo mandar, para que nada quede "procesando" para siempre) y los
  callbacks v1 que lleguen durante la transición.

También vive aquí la resolución de ternas: el envío la usa para armar el job y
el callback para mapear cada terna de vuelta a su articulación, y las dos deben
dar exactamente lo mismo.
"""
import hashlib
import logging
from dataclasses import dataclass

from django.db import transaction
from django.db.models import Q

from telerehabilitation_API.processing.movement import build_movement
from telerehabilitation_API.therapy.models import Exercise, ExerciseResult, ExerciseSkeletonPointTracked
from telerehabilitation_API.therapy.models.exercise_result_point import ExerciseResultPoint

logger = logging.getLogger(__name__)

# Video del paciente (ExerciseResult) y video de referencia (Exercise).
PATIENT = 'result'
REFERENCE = 'exercise'

NO_PERSON_ERROR = 'El video se procesó pero no se detectó a la persona (ángulos nulos).'
MISSING_NEIGHBORS_ERROR = 'Articulación sin vecinos configurados.'
NOT_RETURNED_ERROR = 'El servicio no devolvió esta articulación.'


@dataclass(frozen=True)
class Target:
    """A quién pertenece el video: `kind` es PATIENT o REFERENCE."""
    kind: str
    id: int


class TargetNotFound(Exception):
    """Resultado, ejercicio o articulación inexistente o ajena (el callback responde 404)."""


def resolve_points(tracked):
    """
    Terna [vecino1, vértice, vecino2] de una articulación rastreada, o None si
    no se puede medir (falta un vecino, IDs repetidos o fuera de 0-32).

    Vecino = el personalizado del ejercicio y, si no hay, el del esqueleto. Se
    compara con `is not None` y no con `or`: el id 0 (NOSE) es un vecino válido
    y `or` lo descartaría. Es la misma regla de `points_tracked` en la API.
    """
    skeleton = tracked.skeleton_point

    def neighbor(own_id, default_attr):
        if own_id is not None:
            return own_id
        return getattr(skeleton, default_attr) if skeleton is not None else None

    points = [
        neighbor(tracked.left_point_id, 'left_point_id'),
        tracked.skeleton_point_id,
        neighbor(tracked.right_point_id, 'right_point_id'),
    ]
    if any(p is None or not 0 <= p <= 32 for p in points) or len(set(points)) != 3:
        return None
    return points


def video_digest(video_url):
    """
    Huella corta de la URL del video, que viaja en el resultsEndpoint.

    En v2 la URL del callback ya no lleva `point_tracked`, que era lo que
    permitía rechazar (404) el aviso viejo de un video de referencia
    reemplazado. Cada subida tiene una URL distinta (ruta con UUID), así que la
    huella identifica el video: un aviso con otra huella es de un video viejo.
    """
    return hashlib.sha256((video_url or '').encode('utf-8')).hexdigest()[:16]


def record_job_outcome(target, *, error, joints, frames, duration_s, video_digest_param=None):
    """
    Registra el callback v2 completo de un video.

    - `error`: error del job entero; si viene, todas las articulaciones quedan
      en error (lo ya medido en otras corridas se conserva en el movimiento).
    - `joints`: entradas saneadas del callback, cada una con `triplet` (tupla),
      `index` (posición en `joints` del callback, que es la de los ángulos por
      frame), `max_angle` y `min_angle`.
    - `frames`: frames saneados (ver callbacks.parse_payload_v2).

    Cada articulación rastreada se mapea a su entrada POR LA TERNA COMPLETA
    (la misma `resolve_points` del envío). Dos articulaciones con la misma
    terna reciben la misma entrada.

    La fila padre se bloquea con select_for_update, como en v1: así un
    reintento del mismo aviso no se cruza con otro.
    """
    with transaction.atomic():
        parent = _lock_parent(target)
        if video_digest_param and video_digest_param != video_digest(parent.video):
            # Aviso de un video que ya no es el de este ejercicio/resultado.
            raise TargetNotFound()

        # No `parent.exercise`: una FK directa usa el manager base y cargaría el
        # movimiento de la referencia (varios MB) solo para leer id y nombre.
        exercise = parent if target.kind == REFERENCE else Exercise.objects.filter(pk=parent.exercise_id).first()
        if exercise is None:
            raise TargetNotFound()

        by_triplet = {joint['triplet']: joint for joint in joints}
        codenames_by_joint = {}
        any_error = False
        all_measured = True
        tracked_list = list(
            ExerciseSkeletonPointTracked.objects.filter(exercise=exercise)
            .select_related('skeleton_point').order_by('id')
        )

        for tracked in tracked_list:
            outcome = _joint_outcome(tracked, error, by_triplet, frames)
            if outcome['index'] is not None and tracked.skeleton_point is not None:
                codenames_by_joint.setdefault(outcome['index'], []).append(tracked.skeleton_point.codename)
            any_error = any_error or outcome['error'] is not None
            all_measured = all_measured and outcome['error'] is None
            _store_joint(target, parent, tracked, outcome)

        if error is None:
            parent.movement = build_movement(exercise, frames, duration_s, codenames_by_joint)

        if target.kind == PATIENT:
            parent.status = patient_status(parent)
        elif any_error:
            parent.status = Exercise.ERROR
        else:
            parent.status = Exercise.PROCESSED if (tracked_list and all_measured) else Exercise.PROCESSING
        parent.save(update_fields=['status', 'movement'])


def _joint_outcome(tracked, job_error, by_triplet, frames):
    triplet = resolve_points(tracked)
    if triplet is None:
        return {'index': None, 'max_angle': None, 'min_angle': None, 'angle_series': None,
                'error': MISSING_NEIGHBORS_ERROR}
    if job_error is not None:
        return {'index': None, 'max_angle': None, 'min_angle': None, 'angle_series': None,
                'error': job_error}
    joint = by_triplet.get(tuple(triplet))
    if joint is None:
        return {'index': None, 'max_angle': None, 'min_angle': None, 'angle_series': None,
                'error': NOT_RETURNED_ERROR}

    index = joint['index']
    series = [
        {'t_ms': round(frame['t'] * 1000, 3),
         'angle': frame['angles_2d'][index] if index < len(frame['angles_2d']) else None}
        for frame in frames
    ]
    measured = joint['max_angle'] is not None and joint['min_angle'] is not None
    return {
        'index': index,
        'max_angle': joint['max_angle'],
        'min_angle': joint['min_angle'],
        'angle_series': series,
        'error': None if measured else NO_PERSON_ERROR,
    }


def _store_joint(target, parent, tracked, outcome):
    if target.kind == PATIENT:
        ExerciseResultPoint.objects.update_or_create(
            exercise_result=parent,
            point_tracked=tracked,
            defaults={
                'max_angle': outcome['max_angle'],
                'min_angle': outcome['min_angle'],
                'angle_series': outcome['angle_series'],
                'error': outcome['error'],
            },
        )
        return
    if outcome['error']:
        logger.warning('Articulación %s del ejercicio %s en error: %s',
                       tracked.id, parent.id, outcome['error'])
        return
    tracked.max_angle = outcome['max_angle']
    tracked.min_angle = outcome['min_angle']
    tracked.save(update_fields=['max_angle', 'min_angle'])


def _lock_parent(target):
    model = ExerciseResult if target.kind == PATIENT else Exercise
    parent = model.objects.select_for_update().filter(pk=target.id).first()
    if parent is None:
        raise TargetNotFound()
    return parent


def record_joint_outcome(target, point_tracked_id, *, max_angle=None, min_angle=None,
                         angle_series=None, error=None):
    """
    Guarda el desenlace de una articulación de forma idempotente y recalcula el
    estado del padre.

    Es error de la articulación si `error` no es nulo o si falta max o min (el
    servicio procesó el video sin detectar a la persona).

    La fila padre se bloquea con select_for_update: los callbacks de varias
    articulaciones del mismo video llegan en paralelo, y sin el bloqueo dos de
    ellos podrían leer el estado a la vez y dejar "procesando" un resultado ya
    completo. (SQLite lo ignora; se valida en la prueba de punta a punta.)
    """
    if error is None and (max_angle is None or min_angle is None):
        error = NO_PERSON_ERROR

    with transaction.atomic():
        if target.kind == PATIENT:
            _record_patient(target.id, point_tracked_id, max_angle, min_angle, angle_series, error)
        elif target.kind == REFERENCE:
            _record_reference(target.id, point_tracked_id, max_angle, min_angle, error)
        else:
            raise ValueError(f'Tipo de destino desconocido: {target.kind}')


def _tracked_of_exercise(exercise_id, point_tracked_id):
    # exercise_id puede ser NULL (on_delete=SET_NULL): sin este control, el
    # filtro exercise_id=None encontraría articulaciones huérfanas ajenas.
    if exercise_id is None:
        raise TargetNotFound()
    tracked = ExerciseSkeletonPointTracked.objects.filter(
        pk=point_tracked_id, exercise_id=exercise_id,
    ).first()
    if tracked is None:
        raise TargetNotFound()
    return tracked


def _record_patient(result_id, point_tracked_id, max_angle, min_angle, angle_series, error):
    result = ExerciseResult.objects.select_for_update().filter(pk=result_id).first()
    if result is None:
        raise TargetNotFound()
    tracked = _tracked_of_exercise(result.exercise_id, point_tracked_id)

    ExerciseResultPoint.objects.update_or_create(
        exercise_result=result,
        point_tracked=tracked,
        defaults={
            'max_angle': max_angle,
            'min_angle': min_angle,
            'angle_series': angle_series,
            'error': error,
        },
    )
    result.status = patient_status(result)
    result.save(update_fields=['status'])


def patient_status(result):
    """
    ERROR si alguna articulación falló (lo ya medido se conserva); PROCESSED si
    todas las articulaciones rastreadas tienen max y min; si no, PROCESSING.
    """
    tracked_ids = set(
        ExerciseSkeletonPointTracked.objects.filter(exercise_id=result.exercise_id)
        .values_list('id', flat=True)
    )
    outcomes = ExerciseResultPoint.objects.filter(
        exercise_result=result, point_tracked_id__in=tracked_ids,
    )
    if any(outcome.error for outcome in outcomes):
        return ExerciseResult.ERROR
    measured = {
        outcome.point_tracked_id for outcome in outcomes
        if outcome.max_angle is not None and outcome.min_angle is not None
    }
    if tracked_ids and measured == tracked_ids:
        return ExerciseResult.PROCESSED
    return ExerciseResult.PROCESSING


def _record_reference(exercise_id, point_tracked_id, max_angle, min_angle, error):
    exercise = Exercise.objects.select_for_update().filter(pk=exercise_id).first()
    if exercise is None:
        raise TargetNotFound()
    tracked = _tracked_of_exercise(exercise.id, point_tracked_id)

    if error:
        # Exercise no tiene campo de error por articulación: el error pasa al
        # ejercicio completo y su motivo queda en el log.
        logger.warning('Articulación %s del ejercicio %s en error: %s', tracked.id, exercise.id, error)
        exercise.status = Exercise.ERROR
    else:
        tracked.max_angle = max_angle
        tracked.min_angle = min_angle
        tracked.save(update_fields=['max_angle', 'min_angle'])
        # Un error anterior de otra articulación se mantiene hasta que se suba
        # un video nuevo (que recrea las articulaciones).
        if exercise.status != Exercise.ERROR:
            pending = exercise.tracked_points.filter(Q(max_angle=None) | Q(min_angle=None)).exists()
            exercise.status = Exercise.PROCESSING if pending else Exercise.PROCESSED
    exercise.save(update_fields=['status'])


def mark_without_joints(target):
    """Un video sin articulaciones que medir nunca recibiría callback: queda en error."""
    model = ExerciseResult if target.kind == PATIENT else Exercise
    model.objects.filter(pk=target.id).update(status=model.ERROR)
    logger.warning('%s %s sin articulaciones rastreadas: queda en error', target.kind, target.id)
