from django.contrib.auth.models import Group
from rest_framework import serializers

from telerehabilitation_API.authentication.serializers.permission_serializer import PermissionSerializer


class GroupSerializer(serializers.ModelSerializer):
    permissions = PermissionSerializer(many=True)

    class Meta:
        model = Group
        fields = ['name', 'permissions']
