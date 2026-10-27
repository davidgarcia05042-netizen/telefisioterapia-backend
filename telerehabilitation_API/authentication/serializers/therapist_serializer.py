from rest_framework import serializers

from telerehabilitation_API.authentication.models import Therapist
from telerehabilitation_API.authentication.serializers import UserModelNoLinkSerializer


class TherapistSerializer(serializers.ModelSerializer):
    user = UserModelNoLinkSerializer()

    class Meta:
        model = Therapist
        fields = ['id', 'user']
