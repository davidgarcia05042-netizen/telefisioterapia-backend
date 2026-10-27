from django.db import models

from telerehabilitation_API.therapy.models import ExerciseSkeletonPointTracked, ExerciseResult


class ExerciseResultPoint(models.Model):
    point_tracked = models.ForeignKey(ExerciseSkeletonPointTracked, on_delete=models.SET_NULL, null=True)
    max_angle = models.FloatField(null=True)
    min_angle = models.FloatField(null=True)
    exercise_result = models.ForeignKey(ExerciseResult, on_delete=models.SET_NULL, null=True, related_name='points')
    score = models.PositiveIntegerField(null=True, blank=True)
    # Serie del Servicio de Procesamiento: [{"t_ms": float, "angle": float|null}]
    # por frame. Se guarda completa para análisis como "tiempo dentro del rango
    # esperado", que max/min no permiten.
    angle_series = models.JSONField(null=True, blank=True)
    # Motivo si esta articulación falló (sin vecinos, envío rechazado, video
    # sin persona detectada...). Nulo = sin error.
    error = models.TextField(null=True, blank=True)
