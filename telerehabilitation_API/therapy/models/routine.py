from django.db import models

from telerehabilitation_API.therapy.models import Therapy


class Routine(models.Model):
    therapy = models.ForeignKey(Therapy, on_delete=models.SET_NULL, null=True, related_name='routines')
    name = models.CharField(max_length=128)
    description = models.TextField()
    is_model = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
