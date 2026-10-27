from django.contrib.auth.models import User
from rest_framework import serializers

from telerehabilitation_API.authentication.serializers.group_serializer import GroupSerializer


class UserModelSerializer(serializers.HyperlinkedModelSerializer):
    groups = GroupSerializer(many=True)

    class Meta:
        model = User
        fields = ['url', 'id', 'username', 'email', 'groups', 'first_name', 'last_name']


class UserModelNoLinkSerializer(serializers.ModelSerializer):

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name']
