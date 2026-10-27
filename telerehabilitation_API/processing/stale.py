"""
Envíos huérfanos: un resultado del paciente (ExerciseResult) o un video de
referencia (Exercise) que lleva más de 15 min "procesando" sin callback pasa a
error.

Pasa si el envío se perdió: Render cortó el contenedor a mitad de la subida, el
servicio no llegó a crear el job o nunca avisó. Sin esto quedaba "procesando"
para siempre y el paciente no podía volver a grabar.

Se comprueba al leer, en los GET que ya consultan la app y el panel
(GET /exercise_results y GET /exercises/), con un UPDATE condicional: no hace
falta cron ni otra infraestructura.

Un callback que llegue tarde se sigue aceptando y deja el resultado como
procesado: record_job_outcome reescribe todas las articulaciones y recalcula
el estado, sin mirar el anterior.
"""
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from telerehabilitation_API.therapy.models import Exercise, ExerciseResult, ExerciseSkeletonPointTracked
from telerehabilitation_API.therapy.models.exercise_result_point import ExerciseResultPoint

logger = logging.getLogger(__name__)

STALE_AFTER = timedelta(minutes=15)
STALE_ERROR = 'No se completó el procesamiento; vuelve a enviar el video'


def expire_stale_processing(now=None):
    """Pasa a error lo que lleva más de STALE_AFTER procesando. Devuelve (resultados, referencias)."""
    cutoff = (now or timezone.now()) - STALE_AFTER
    return _expire_results(cutoff), _expire_references(cutoff)


def _expire_results(cutoff):
    stale = ExerciseResult.objects.filter(status=ExerciseResult.PROCESSING, start_time__lt=cutoff)
    expired = 0
    for result_id, exercise_id in stale.values_list('id', 'exercise_id'):
        with transaction.atomic():
            # UPDATE condicional: si un callback lo cerró entre la consulta y
            # aquí, no cambia nada. Si cambia, el bloqueo de la fila dura hasta
            # el commit, así que un callback que llegue ahora espera y después
            # escribe lo suyo encima.
            changed = ExerciseResult.objects.filter(
                pk=result_id, status=ExerciseResult.PROCESSING, start_time__lt=cutoff,
            ).update(status=ExerciseResult.ERROR)
            if not changed:
                continue
            _mark_unmeasured_joints(result_id, exercise_id)
        expired += 1
        logger.warning('Resultado %s: %s min procesando sin callback, pasa a error',
                       result_id, int(STALE_AFTER.total_seconds() // 60))
    return expired


def _mark_unmeasured_joints(result_id, exercise_id):
    """El motivo, en cada articulación sin medición ni error (lo ya medido se conserva)."""
    if exercise_id is None:
        return
    for tracked_id in ExerciseSkeletonPointTracked.objects.filter(exercise_id=exercise_id).values_list('id', flat=True):
        point = ExerciseResultPoint.objects.filter(exercise_result_id=result_id, point_tracked_id=tracked_id).first()
        if point is None:
            ExerciseResultPoint.objects.create(
                exercise_result_id=result_id, point_tracked_id=tracked_id, error=STALE_ERROR,
            )
        elif not point.error and (point.max_angle is None or point.min_angle is None):
            point.error = STALE_ERROR
            point.save(update_fields=['error'])


def _expire_references(cutoff):
    stale = Exercise.objects.filter(status=Exercise.PROCESSING, processing_started_at__lt=cutoff)
    ids = list(stale.values_list('id', flat=True))
    if not ids:
        return 0
    # Exercise no tiene campo para el motivo: queda en el log, como los demás
    # errores de la referencia (results._record_reference).
    expired = Exercise.objects.filter(
        pk__in=ids, status=Exercise.PROCESSING, processing_started_at__lt=cutoff,
    ).update(status=Exercise.ERROR)
    if expired:
        logger.warning('Referencias %s: %s', ids, STALE_ERROR)
    return expired
