from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from telerehabilitation_API.authentication.permissions import IsAdminTherapistOrPatient
from telerehabilitation_API.therapy.models import ScheduledTrainingDifficulty
from telerehabilitation_API.therapy.ownership import as_id, scheduled_trainings_for
from telerehabilitation_API.therapy.querysets import exercise_lookups, routine_lookups
from telerehabilitation_API.therapy.serializers.scheduled_training_diff_serializer import \
    ScheduledTrainingDiffSerializer


class ScheduledTrainingDiffViewSet(APIView):
    """
    Dificultad asignada a un ejercicio en un entrenamiento programado. La lee
    el resultado del paciente en el panel y, desde M1 (docs/DECISIONES_TECNICAS.md), la app
    del paciente para mostrar el rango esperado. En la plataforma original no
    exigía autenticación (hallazgo, docs/DECISIONES_TECNICAS.md).

    Admin y Therapist ven cualquiera; el paciente, solo las de sus rutinas
    programadas (si no es suya, 404).
    """
    permission_classes = [IsAuthenticated, IsAdminTherapistOrPatient]

    def get(self, request):
        scheduled_training_id = as_id(request.GET.get('scheduled_training_id'))
        exercise_id = as_id(request.GET.get('exercise_id'))
        if scheduled_training_id is None or exercise_id is None:
            return Response(status=400)

        if not scheduled_trainings_for(request.user).filter(pk=scheduled_training_id).exists():
            return Response({'detail': 'No encontrado.'}, status=404)

        # `exercise` es una FK directa (manager base): se aplaza su movimiento
        # aquí. La rutina programada se serializa completa, con sus ejercicios.
        assignment = ScheduledTrainingDifficulty.objects.filter(
            scheduled_training_id=scheduled_training_id,
            exercise_id=exercise_id,
        ).select_related('exercise', 'scheduled_training__routine__therapy').defer(
            'exercise__movement',
        ).prefetch_related(
            'difficulty__ranges__point_tracked__skeleton_point',
            *exercise_lookups('exercise__'),
            *routine_lookups('scheduled_training__routine__'),
        ).first()
        if assignment is None:
            # Serializar None daba {}: el panel no distinguía "sin dificultad"
            # de una respuesta rota.
            return Response({'difficulty': None}, status=200)
        return Response(
            ScheduledTrainingDiffSerializer(assignment, context={'request': request}).data,
            status=200
        )
