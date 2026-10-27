from django.db import models

from .managers import DeferMovementManager

from . import Exercise
from .training import Training


def upload_exercise_video_to(instance, filename):
    return 'exercise_result_{}/{}'.format(instance.id, filename)


class ExerciseResult(models.Model):
    NO_VIDEO = "1"
    PROCESSING = "2"
    PROCESSED = "3"
    ERROR = "4"
    EXERCISE_STATUS = [
        (NO_VIDEO, "Sin video"),
        (PROCESSING, "Video en procesamiento"),
        (PROCESSED, "Video procesado"),
        (ERROR, "Error al procesar video")
    ]
    exercise = models.ForeignKey(Exercise, on_delete=models.SET_NULL, null=True, related_name='results')
    training = models.ForeignKey(Training, on_delete=models.SET_NULL, null=True, related_name='results')
    start_time = models.DateTimeField(null=True)
    video = models.URLField(max_length=512, blank=True, null=True)
    status = models.CharField(max_length=32, choices=EXERCISE_STATUS, default=NO_VIDEO)
    concept = models.TextField(null=True, blank=True)
    score = models.PositiveIntegerField(null=True, blank=True)
    # Movimiento medido por el Servicio de Procesamiento (docs/DECISIONES_TECNICAS.md, D4),
    # con la estructura de JSON_MOVIMIENTO_ESTRUCTURA.md del panel. Pesa varios MB:
    # ningún serializer de listados lo incluye; se sirve en un endpoint aparte.
    movement = models.JSONField(null=True, blank=True)

    # Ningún queryset carga `movement` salvo que se pida (ver managers.py).
    objects = DeferMovementManager()
