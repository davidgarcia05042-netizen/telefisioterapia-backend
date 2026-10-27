from django.contrib.auth import authenticate
from rest_framework import serializers

from telerehabilitation_API.authentication.token_auth import valid_token_for


class UserLoginSerializer(serializers.Serializer):
    username = serializers.CharField(min_length=0, max_length=64)
    password = serializers.CharField(min_length=8, max_length=64)

    def validate(self, data):
        # Sin print(): el original escribía el usuario y la contraseña en los
        # logs del servidor.
        user = authenticate(username=data['username'], password=data['password'])
        if not user:
            raise serializers.ValidationError('The credentials are not valid')

        self.context['user'] = user
        return data

    def create(self, data):
        """Recuperar el token vigente del usuario, o uno nuevo si venció."""
        token = valid_token_for(self.context['user'])
        return self.context['user'], token.key
