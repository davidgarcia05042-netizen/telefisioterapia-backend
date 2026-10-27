from django.db import models

from telerehabilitation_API.therapy.models import ExerciseSkeletonPointTracked, ExerciseDifficulty


class DifficultyRange(models.Model):
    point_tracked = models.ForeignKey(ExerciseSkeletonPointTracked, on_delete=models.SET_NULL, null=True)
    difficulty = models.ForeignKey(ExerciseDifficulty, on_delete=models.SET_NULL, null=True, related_name='ranges')
    max_angle = models.FloatField(null=True)
    min_angle = models.FloatField(null=True)
