from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from telerehabilitation_API.authentication.accounts import AccountError, create_account, created_response
from telerehabilitation_API.authentication.models import Admin
from telerehabilitation_API.authentication.permissions import IsAdmin
from telerehabilitation_API.authentication.serializers.admin_serializer import AdminSerializer


class AdminViewSet(APIView):
    # Solo administradores, para listar y para crear. Antes la vista no exigía
    # ni autenticación: cualquiera podía crearse una cuenta del grupo "Admin".
    permission_classes = [IsAuthenticated, IsAdmin]

    def get(self, request, format=None):
        return Response(AdminSerializer(Admin.objects.all(), many=True).data)

    def post(self, request):
        # B1 (docs/DECISIONES_TECNICAS.md): contraseña temporal aleatoria, no la de siempre del original.
        try:
            user, password = create_account(request.data, 'Admin', Admin)
        except AccountError as error:
            return Response({'detail': str(error)}, status=400)
        return created_response(user, password)

