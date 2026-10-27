from rest_framework import serializers

from telerehabilitation_API.authentication.serializers import TherapistSerializer, PatientSerializer
from telerehabilitation_API.therapy.models import TherapyPatient
from telerehabilitation_API.therapy.serializers import TherapySerializer


class TherapyPatientSerializer(serializers.ModelSerializer):
    therapy = TherapySerializer(required=False)
    therapist = TherapistSerializer(required=False)
    patient = PatientSerializer(required=False)

    class Meta:
        model = TherapyPatient
        fields = ['id', 'therapy', 'therapist', 'patient', 'scheduled_trainings']
