from rest_framework.views import APIView

from telerehabilitation_API.therapy.models import TherapyPatient


class TherapyPatientViewSet(APIView):
    def get(self, request):
        pass
