import pytz
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from telerehabilitation_API.authentication.permissions import IsAdminOrTherapist

from telerehabilitation_API.authentication.serializers import UserModelSerializer
from telerehabilitation_API.therapy.models import TherapyPatient, ScheduledTraining
from telerehabilitation_API.therapy.querysets import with_schedule_details
from telerehabilitation_API.therapy.serializers import TherapyPatientSerializer, RoutineSerializer


class TherapistPatientsViewSet(APIView):
    # M1 (docs/DECISIONES_TECNICAS.md): datos de pacientes y terapeutas, solo para el panel.
    # Antes no exigía autenticación.
    permission_classes = [IsAuthenticated, IsAdminOrTherapist]

    def get(self, request):
        therapist_id = request.GET.get('therapist_id', 'NOT_FOUND')
        patient_id = request.GET.get('patient_id', 'NOT_FOUND')
        patient_user_id = request.GET.get('patient_user_id', 'NOT_FOUND')
        if therapist_id == 'NOT_FOUND':
            if patient_id == 'NOT_FOUND':
                if patient_user_id == 'NOT_FOUND':
                    return Response([], status=400)
                else:
                    try:
                        patients_json = TherapyPatientSerializer(
                            TherapyPatient.objects.filter(patient__user_id=int(patient_user_id)),
                            many=True,
                            context={'request': request}
                        ).data

                        return Response(patients_json, status=200)
                    except TherapyPatient.DoesNotExist:
                        return Response([], status=404)
            try:
                patients_json = TherapyPatientSerializer(
                    TherapyPatient.objects.get(pk=int(patient_id)),
                    context={'request': request}
                ).data

                scheduled_trainings = with_schedule_details(
                    ScheduledTraining.objects.filter(therapy_patient_id=int(patient_id)))
                if scheduled_trainings:

                    patients_json['scheduled_trainings'] = [{
                        'id': scheduled_training.id,
                        'routine': RoutineSerializer(scheduled_training.routine, context={'request': request}).data,
                        'start_time': scheduled_training.start_time.astimezone(pytz.timezone('America/Bogota')),
                        'status': scheduled_training.status
                    } for scheduled_training in scheduled_trainings]

                return Response(patients_json, status=200)
            except TherapyPatient.DoesNotExist:
                return Response([], status=404)
        else:
            try:
                patients_json = TherapyPatientSerializer(
                    TherapyPatient.objects.filter(therapist__user_id=int(therapist_id)).all(),
                    many=True,
                    context={'request': request}
                ).data

                return Response(patients_json, status=200)
            except TherapyPatient.DoesNotExist:
                return Response([], status=404)
