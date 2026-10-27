from django.db import models

from .managers import DeferMovementManager

from . import Routine


def upload_exercise_video_to(instance, filename):
    return 'exercise_{}/{}'.format(instance.id, filename)


class Exercise(models.Model):
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
    routine = models.ForeignKey(Routine, on_delete=models.SET_NULL, null=True, related_name='exercises')
    name = models.CharField(max_length=128)
    description = models.TextField()
    order = models.IntegerField()
    video = models.URLField(max_length=512, blank=True, null=True)
    status = models.CharField(max_length=32, choices=EXERCISE_STATUS, default=NO_VIDEO)
    is_model = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    # Movimiento medido por el Servicio de Procesamiento (docs/DECISIONES_TECNICAS.md, D4),
    # con la estructura de JSON_MOVIMIENTO_ESTRUCTURA.md del panel. Pesa varios MB:
    # ningún serializer de listados lo incluye; se sirve en un endpoint aparte.
    movement = models.JSONField(null=True, blank=True)
    # Cuándo se envió el video de referencia actual al servicio. Con él, una
    # referencia que lleva más de 15 min "procesando" sin callback pasa a error
    # (processing/stale.py). El resultado del paciente usa su start_time.
    processing_started_at = models.DateTimeField(null=True, blank=True)

    # Ningún queryset carga `movement` salvo que se pida (ver managers.py).
    objects = DeferMovementManager()
