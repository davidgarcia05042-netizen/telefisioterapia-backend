import pytz

from drf_yasg import openapi
from drf_yasg.utils import swagger_auto_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import serializers # Import serializers

from telerehabilitation_API.authentication.permissions import (
    IsAdminOrTherapist, IsAdminTherapistOrPatient, is_staff_member,
)
from telerehabilitation_API.therapy.ownership import as_id, scheduled_trainings_for

from telerehabilitation_API.authentication.serializers import PatientSerializer, TherapistSerializer
from telerehabilitation_API.therapy.models import ScheduledTraining
from telerehabilitation_API.therapy.querysets import with_schedule_details
from telerehabilitation_API.therapy.scheduled_assignment import AssignmentError, create_assignment, parse_assignment
from telerehabilitation_API.therapy.serializers import RoutineSerializer

# Define un Serializer auxiliar para la documentación de la respuesta del método GET
class ScheduledTrainingGetResponseBodySerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True, help_text="ID del entrenamiento programado")
    routine = RoutineSerializer(read_only=True, help_text="Detalles de la rutina asociada")
    start_time = serializers.DateTimeField(read_only=True, help_text="Hora de inicio del entrenamiento (zona horaria America/Bogota)")
    status = serializers.CharField(read_only=True, help_text="Código de estado del entrenamiento (ej. 'PENDING', 'COMPLETED')")
    status_verbose = serializers.CharField(read_only=True, help_text="Estado del entrenamiento legible para humanos")
    patient = PatientSerializer(read_only=True, required=False, help_text="Detalles del paciente (presente cuando se filtra por routine_id)")
    therapist = TherapistSerializer(read_only=True, required=False, help_text="Detalles del terapeuta (presente cuando se filtra por routine_id)")

# Define un Serializer para el cuerpo de la petición POST
class ScheduledTrainingPostRequestBodySerializer(serializers.Serializer):
    routine = serializers.IntegerField(help_text="ID de la rutina a programar")
    therapy_patient = serializers.IntegerField(help_text="ID del paciente de terapia al que se asigna el entrenamiento")
    start_time = serializers.CharField(help_text="Fecha y hora de inicio del entrenamiento (formato 'DD/MM/YYYY HH:MM:SS')")
    difficulties = serializers.ListField(
        child=serializers.DictField(
            child=serializers.IntegerField(),
            help_text="Lista de objetos de dificultad, cada uno con 'diff' (ID de dificultad) y 'exercise_id' (ID de ejercicio)"
        ),
        help_text="Lista de dificultades asociadas a ejercicios específicos para este entrenamiento"
    )

# Define un Serializer para la respuesta del método POST (vacía)
class ScheduledTrainingPostResponseBodySerializer(serializers.Serializer):
    # El método POST devuelve un objeto vacío en caso de éxito
    class Meta:
        ref_name = None # Evita que drf_yasg genere un nombre de referencia para un objeto vacío


class ScheduledTrainingViewSet(APIView):
    """
    Rutinas programadas. Antes el GET y el POST no exigían autenticación.

    M1 (docs/DECISIONES_TECNICAS.md):
    - GET sin parámetros: las del usuario de la sesión (la app del paciente).
    - GET ?scheduled_training_id=: el panel, o el paciente si es suya (si no, 404).
    - GET ?patient_id= / ?routine_id=: solo el panel.
    - POST: solo el panel.
    """

    def get_permissions(self):
        if self.request.method == 'GET':
            return [IsAuthenticated(), IsAdminTherapistOrPatient()]
        return [IsAuthenticated(), IsAdminOrTherapist()]

    @swagger_auto_schema(
        operation_summary="Retrieve scheduled trainings",
        operation_description="""
        Retrieves a list of scheduled trainings or a single scheduled training instance.

        The behavior of this endpoint varies based on the provided query parameters:

        - **`patient_id`**: If provided, returns all scheduled trainings for that specific patient.
        - **`scheduled_training_id`**: If provided, returns the details of a single scheduled training.
        - **`routine_id`**: If provided, returns all scheduled trainings associated with a specific routine, including patient and therapist details.
        - **No parameters**: If no specific ID is provided, it returns all scheduled trainings for the currently authenticated user (patient).
        """,
        manual_parameters=[
            openapi.Parameter('routine_id', openapi.IN_QUERY, description="Filter by routine ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('scheduled_training_id', openapi.IN_QUERY, description="Get by scheduled training ID", type=openapi.TYPE_INTEGER),
            openapi.Parameter('patient_id', openapi.IN_QUERY, description="Filter by patient ID", type=openapi.TYPE_INTEGER),
        ],
        responses={
            200: openapi.Response(
                description="Successful retrieval of scheduled training(s). Can be a list or a single object depending on query parameters.",
                schema=ScheduledTrainingGetResponseBodySerializer(many=True)
            ),
            404: "Not Found: No scheduled training(s) matching the criteria were found."
        }
    )
    def get(self, request):
        routine_id = request.GET.get('routine_id', 'NOT_FOUND')
        scheduled_training_id = request.GET.get('scheduled_training_id', 'NOT_FOUND')
        patient_id = request.GET.get('patient_id', 'NOT_FOUND')

        # Consultar por paciente o por rutina es cosa del panel.
        if (patient_id != 'NOT_FOUND' or routine_id != 'NOT_FOUND') and not is_staff_member(request.user):
            return Response({'detail': 'No tienes permiso para realizar esta acción.'}, status=403)

        if patient_id != 'NOT_FOUND':
            try:
                scheduled_trainings = with_schedule_details(
                    ScheduledTraining.objects.filter(therapy_patient_id=int(patient_id)))
                if scheduled_trainings:

                    return Response([{
                        'id': scheduled_training.id,
                        'routine': RoutineSerializer(scheduled_training.routine, context={'request': request}).data,
                        'start_time': scheduled_training.start_time.astimezone(pytz.timezone('America/Bogota')),
                        'status': scheduled_training.status,
                        'status_verbose': next(status[1] for status in ScheduledTraining.SCHEDULED_TRAINING_STATUS if
                                               status[0] == scheduled_training.status)
                    } for scheduled_training in scheduled_trainings], status=200)
                else:
                    return Response(status=404)
            except ScheduledTraining.DoesNotExist:
                return Response(status=404)

        if scheduled_training_id != 'NOT_FOUND':
            try:
                # El paciente solo ve las suyas: si no es suya, 404 (no se revela que existe).
                scheduled_training = with_schedule_details(scheduled_trainings_for(request.user)).get(
                    pk=as_id(scheduled_training_id))
                if scheduled_training:

                    return Response({
                        'id': scheduled_training.id,
                        'routine': RoutineSerializer(scheduled_training.routine, context={'request': request}).data,
                        'start_time': scheduled_training.start_time.astimezone(pytz.timezone('America/Bogota')),
                        'status': scheduled_training.status,
                        'status_verbose': next(status[1] for status in ScheduledTraining.SCHEDULED_TRAINING_STATUS if
                                               status[0] == scheduled_training.status)
                    }, status=200)
                else:
                    return Response(status=404)
            except ScheduledTraining.DoesNotExist:
                return Response(status=404)

        if routine_id != 'NOT_FOUND':
            try:
                scheduled_trainings = with_schedule_details(ScheduledTraining.objects.filter(routine__id=routine_id))
                if scheduled_trainings:
                    return Response([{
                        'id': scheduled_training.id,
                        'routine': RoutineSerializer(scheduled_training.routine, context={'request': request}).data,
                        'start_time': scheduled_training.start_time.astimezone(pytz.timezone('America/Bogota')),
                        'patient': PatientSerializer(scheduled_training.therapy_patient.patient, context={'request': request}).data,
                        'therapist': TherapistSerializer(scheduled_training.therapy_patient.therapist, context={'request': request}).data,
                    } for scheduled_training in scheduled_trainings], status=200)
                else:
                    return Response(status=404)
            except ScheduledTraining.DoesNotExist:
                return Response(status=404)

        try:
            # La app del paciente: cada rutina programada lleva la rutina completa.
            scheduled_trainings = with_schedule_details(ScheduledTraining.objects.filter(
                therapy_patient__patient__user_id=request.user.id))
            if scheduled_trainings:

                return Response([{
                    'id': scheduled_training.id,
                    'routine': RoutineSerializer(scheduled_training.routine, context={'request': request}).data,
                    'start_time': scheduled_training.start_time.astimezone(pytz.timezone('America/Bogota')),
                    'status': scheduled_training.status
                } for scheduled_training in scheduled_trainings], status=200)
            else:
                return Response(status=404)
        except ScheduledTraining.DoesNotExist:
            return Response(status=404)

    @swagger_auto_schema(
        operation_summary="Create a new scheduled training",
        operation_description="Creates a new scheduled training instance with specified routine, patient, start time, and associated difficulties.",
        request_body=ScheduledTrainingPostRequestBodySerializer,
        responses={
            201: openapi.Response(
                description="Scheduled training created successfully.",
                schema=ScheduledTrainingPostResponseBodySerializer
            ),
            400: "Bad Request: Invalid data provided ({'detail': mensaje})."
        }
    )
    def post(self, request):
        # Se valida todo antes de escribir y se crea dentro de una transacción
        # (therapy/scheduled_assignment.py): nada queda a medias.
        try:
            data = parse_assignment(request.data)
        except AssignmentError as error:
            return Response({'detail': str(error)}, status=400)
        create_assignment(data)
        return Response({}, status=201)
