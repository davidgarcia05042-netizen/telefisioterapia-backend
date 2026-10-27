from django.contrib.auth.models import User
from django.db import models


class Admin(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    class Meta:
        db_table = 'admin_user'


class Therapist(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)

    class Meta:
        db_table = 'therapist_user'


class Patient(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    birth_date = models.DateField(null=True, blank=True)
    height = models.FloatField(null=True, blank=True)
    weight = models.FloatField(null=True, blank=True)
    gender = models.CharField(max_length=50, null=True, blank=True)
    description = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'patient_user'
