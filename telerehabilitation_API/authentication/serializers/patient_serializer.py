from rest_framework import serializers

from telerehabilitation_API.authentication.models import Patient
from telerehabilitation_API.authentication.serializers import UserModelNoLinkSerializer


class PatientSerializer(serializers.ModelSerializer):
    user = UserModelNoLinkSerializer()

    class Meta:
        model = Patient
        fields = ['id', 'user', 'birth_date', 'height', 'weight', 'gender', 'description']
