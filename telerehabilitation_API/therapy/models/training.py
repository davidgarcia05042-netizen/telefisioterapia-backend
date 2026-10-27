from django.db import models

from telerehabilitation_API.therapy.models import ScheduledTraining


class Training(models.Model):
    schedule_training = models.ForeignKey(ScheduledTraining, on_delete=models.CASCADE)
    start_time = models.DateTimeField(null=True)
    end_time = models.DateTimeField(null=True)
