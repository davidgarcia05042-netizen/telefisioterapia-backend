from django.db import models

from telerehabilitation_API.therapy.models import Exercise


class ExerciseDifficulty(models.Model):
    exercise = models.ForeignKey(Exercise, on_delete=models.SET_NULL, null=True, related_name='difficulties')
    name = models.CharField(max_length=128)
    description = models.TextField()
