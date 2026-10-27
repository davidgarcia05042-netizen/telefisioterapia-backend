from django.db import models

from telerehabilitation_API.authentication.models import Therapist, Patient
from telerehabilitation_API.therapy.models import Therapy


class TherapyPatient(models.Model):
    therapy = models.ForeignKey(Therapy, on_delete=models.CASCADE, related_name='patients')
    therapist = models.ForeignKey(Therapist, on_delete=models.SET_NULL, related_name='patients', null=True)
    patient = models.ForeignKey(Patient, on_delete=models.SET_NULL, related_name='therapies', null=True)
