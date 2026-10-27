from rest_framework import serializers

from telerehabilitation_API.authentication.models import Admin
from telerehabilitation_API.authentication.serializers import UserModelNoLinkSerializer


class AdminSerializer(serializers.ModelSerializer):
    user = UserModelNoLinkSerializer()

    class Meta:
        model = Admin
        fields = ['id', 'user']
