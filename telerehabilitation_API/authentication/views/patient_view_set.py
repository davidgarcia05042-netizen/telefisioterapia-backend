from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from telerehabilitation_API.authentication.accounts import AccountError, create_account, created_response
from telerehabilitation_API.authentication.models import Patient
from telerehabilitation_API.authentication.permissions import IsAdmin, IsAdminOrTherapist
from telerehabilitation_API.authentication.serializers import PatientSerializer


class PatientViewSet(APIView):
    def get_permissions(self):
        # Crear usuarios: solo administradores. Listar: también terapeutas,
        # que los eligen al inscribir pacientes (enroll-patient.vue,
        # edit_therapy.vue). Antes la vista no exigía ni autenticación.
        if self.request.method == 'GET':
            return [IsAuthenticated(), IsAdminOrTherapist()]
        return [IsAuthenticated(), IsAdmin()]

    def get(self, request, format=None):
        return Response(PatientSerializer(Patient.objects.all(), many=True).data)

    def post(self, request):
        # B1 (docs/DECISIONES_TECNICAS.md): contraseña temporal aleatoria, no la de siempre del original.
        try:
            user, password = create_account(
                request.data, 'Patient', Patient,
                birth_date=request.data.get('birth_date'),
                height=request.data.get('height'),
                weight=request.data.get('weight'),
                gender=request.data.get('gender'),
                description=request.data.get('description'),
            )
        except AccountError as error:
            return Response({'detail': str(error)}, status=400)
        return created_response(user, password)
