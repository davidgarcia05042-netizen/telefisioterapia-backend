from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils import translation
from rest_framework import permissions, viewsets, status
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action
from rest_framework.response import Response

from telerehabilitation_API.authentication.permissions import is_staff_member
from telerehabilitation_API.authentication.serializers import UserLoginSerializer, UserModelSerializer


class UserViewSet(viewsets.ReadOnlyModelViewSet):
    """
    /auth/users/: login, logout, cambio de contraseña y consulta de usuarios.

    La lista y el detalle estaban abiertos sin token (lista de usuarios con sus
    correos). Ahora exigen sesión: administradores y terapeutas ven todos; los
    demás, solo su propio usuario (M1, docs/DECISIONES_TECNICAS.md).
    """
    queryset = User.objects.all()
    serializer_class = UserModelSerializer

    def get_queryset(self):
        if is_staff_member(self.request.user):
            return User.objects.all()
        return User.objects.filter(pk=self.request.user.pk)

    # Sin autenticación: un token vencido en el encabezado no debe impedir
    # iniciar sesión de nuevo.
    @action(detail=False, methods=['post'], permission_classes=[permissions.AllowAny], authentication_classes=[])
    def login(self, request):
        """User sign in."""
        serializer = UserLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user, token = serializer.save()
        data = {
            'user': UserModelSerializer(user, context={'request': request}).data,
            'access_token': token
        }
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def logout(self, request):
        """
        POST /auth/users/logout/: revoca el token del usuario. `authtoken` guarda
        uno solo por usuario, así que cierra la sesión en todos los navegadores
        donde la tenga abierta.
        """
        Token.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def change_password(self, request):
        """
        POST /auth/users/change_password/ {password, new_password[, username]}.

        Antes no exigía sesión ni validaba la contraseña nueva. Ahora cambia la
        del usuario de la sesión: exige la actual y valida la nueva con los
        validadores de Django (largo, común, solo números, parecida al usuario).
        `username` es opcional (el panel lo envía); si viene, debe ser el de la
        sesión.
        """
        user = request.user
        username = request.data.get('username')
        if username not in (None, '', user.username):
            return Response({'detail': 'Solo puedes cambiar tu propia contraseña.'}, status=status.HTTP_403_FORBIDDEN)
        current = request.data.get('password')
        new = request.data.get('new_password')
        if not isinstance(current, str) or not user.check_password(current):
            return Response({'detail': 'La contraseña actual no es correcta.'}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(new, str) or not new:
            return Response({'detail': 'Escribe la contraseña nueva.'}, status=status.HTTP_400_BAD_REQUEST)
        if new == current:
            return Response({'detail': 'La contraseña nueva debe ser distinta de la actual.'},
                            status=status.HTTP_400_BAD_REQUEST)
        # LANGUAGE_CODE es en-us: los mensajes de los validadores, en español.
        with translation.override('es'):
            try:
                validate_password(new, user=user)
            except ValidationError as error:
                message = ' '.join(str(m) for m in error.messages)
                return Response({'detail': message}, status=status.HTTP_400_BAD_REQUEST)
        user.set_password(new)
        user.save(update_fields=['password'])
        return Response({}, status=status.HTTP_201_CREATED)
