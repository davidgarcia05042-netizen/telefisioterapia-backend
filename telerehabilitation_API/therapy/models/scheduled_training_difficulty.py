from django.db import models

from telerehabilitation_API.therapy.models import ExerciseDifficulty, Exercise, ScheduledTraining


class ScheduledTrainingDifficulty(models.Model):
    difficulty = models.ForeignKey(ExerciseDifficulty, on_delete=models.DO_NOTHING)
    exercise = models.ForeignKey(Exercise, on_delete=models.DO_NOTHING)
    scheduled_training = models.ForeignKey(ScheduledTraining, on_delete=models.DO_NOTHING)

