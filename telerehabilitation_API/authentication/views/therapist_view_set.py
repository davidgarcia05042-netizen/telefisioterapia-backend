from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from telerehabilitation_API.authentication.accounts import AccountError, create_account, created_response
from telerehabilitation_API.authentication.models import Therapist
from telerehabilitation_API.authentication.permissions import IsAdmin, IsAdminOrTherapist
from telerehabilitation_API.authentication.serializers import TherapistSerializer


class TherapistViewSet(APIView):
    def get_permissions(self):
        # Crear usuarios: solo administradores. Listar: también terapeutas,
        # que los eligen al inscribir pacientes (enroll-patient.vue,
        # edit_therapy.vue). Antes la vista no exigía ni autenticación.
        if self.request.method == 'GET':
            return [IsAuthenticated(), IsAdminOrTherapist()]
        return [IsAuthenticated(), IsAdmin()]

    def get(self, request, format=None):
        return Response(TherapistSerializer(Therapist.objects.all(), many=True).data)

    def post(self, request):
        # B1 (docs/DECISIONES_TECNICAS.md): contraseña temporal aleatoria, no la de siempre del original.
        try:
            user, password = create_account(request.data, 'Therapist', Therapist)
        except AccountError as error:
            return Response({'detail': str(error)}, status=400)
        return created_response(user, password)
