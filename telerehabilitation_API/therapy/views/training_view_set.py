import pytz
from django.utils import timezone
from django.utils.timezone import localdate, localtime, now
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from telerehabilitation_API.authentication.permissions import IsAdminOrTherapist, IsAdminTherapistOrPatient
from telerehabilitation_API.therapy.models import Training, ScheduledTraining
from telerehabilitation_API.therapy.ownership import as_id, scheduled_trainings_for, trainings_for
from telerehabilitation_API.therapy.querysets import with_training_details
from telerehabilitation_API.therapy.serializers.training_serializer import TrainingSerializer


class TrainingViewSet(APIView):
    """
    Sesiones de entrenamiento (M1, docs/DECISIONES_TECNICAS.md). Antes el GET no exigía
    autenticación. GET: solo el panel. POST: el panel, o el paciente sobre
    una rutina programada suya (si no es suya, 404).
    """

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAuthenticated(), IsAdminTherapistOrPatient()]
        return [IsAuthenticated(), IsAdminOrTherapist()]

    def get(self, request):
        training_id = request.GET.get('training_id', 'NOT_FOUND')
        scheduled_training_id = request.GET.get('scheduled_training_id', 'NOT_FOUND')
        if training_id != 'NOT_FOUND':
            try:
                return Response(
                    TrainingSerializer(
                        with_training_details(Training.objects.all()).get(pk=int(training_id)),
                        context={'request': request}
                    ).data,
                    status=status.HTTP_200_OK
                )
            except Training.DoesNotExist:
                return Response({}, status=status.HTTP_404_NOT_FOUND)

        if scheduled_training_id != 'NOT_FOUND':
            try:
                return Response(
                    TrainingSerializer(
                        with_training_details(
                            Training.objects.filter(schedule_training_id=int(scheduled_training_id))
                        ).first(),
                        context={'request': request}
                    ).data,
                    status=status.HTTP_200_OK
                )
            except Training.DoesNotExist:
                return Response({}, status=status.HTTP_404_NOT_FOUND)

        try:
            return Response(
                TrainingSerializer(
                    with_training_details(Training.objects.all()),
                    many=True,
                    context={'request': request}
                ).data,
                status=status.HTTP_200_OK
            )
        except Training.DoesNotExist:
            return Response({}, status=status.HTTP_404_NOT_FOUND)

    def post(self, request):
        # Crea la sesión de la rutina programada, o devuelve la que ya existe.
        # Primero se busca la rutina programada: el original creaba la sesión
        # aunque no existiera y después respondía 400.
        schedule_training_id = as_id(request.data.get('schedule_training_id'))
        if schedule_training_id is None:
            return Response({'detail': 'Falta schedule_training_id.'}, status=status.HTTP_400_BAD_REQUEST)
        schedule_training = scheduled_trainings_for(request.user).filter(pk=schedule_training_id).first()
        if schedule_training is None:
            # Inexistente o de otro paciente: 404 (no se revela que existe).
            return Response({'detail': 'No encontrado.'}, status=status.HTTP_404_NOT_FOUND)
        old_training = Training.objects.filter(schedule_training=schedule_training).first()
        if old_training is not None:
            return Response({'id': old_training.id}, status=status.HTTP_201_CREATED)
        new_training = Training.objects.create(
            schedule_training=schedule_training,
            start_time=timezone.localtime(timezone.now(), pytz.timezone('America/Bogota'))
        )
        schedule_training.status = ScheduledTraining.STARTED
        schedule_training.save(update_fields=['status'])
        return Response({'id': new_training.id}, status=status.HTTP_201_CREATED)


class TrainingEndViewSet(APIView):
    """
    POST /training/end?training_id=: cierra la sesión y marca la rutina como
    finalizada. Antes no exigía autenticación. El panel, o el paciente sobre
    una sesión suya (si no es suya, 404).
    """
    permission_classes = [IsAuthenticated, IsAdminTherapistOrPatient]

    def post(self, request):
        training_id = request.GET.get('training_id', 'NOT_FOUND')
        if training_id != 'NOT_FOUND':
            try:
                training = trainings_for(request.user).get(pk=as_id(training_id))
                training.end_time = timezone.localtime(timezone.now(), pytz.timezone('America/Bogota'))
                training.save()
                try:
                    schedule_training = ScheduledTraining.objects.get(pk=training.schedule_training_id)
                    schedule_training.status = ScheduledTraining.FINISHED
                    schedule_training.save()
                except ScheduledTraining.DoesNotExist:
                    return Response({}, status=status.HTTP_400_BAD_REQUEST)
                return Response({}, status=status.HTTP_200_OK)
            except Training.DoesNotExist:
                return Response({}, status=status.HTTP_404_NOT_FOUND)
        return Response({}, status=status.HTTP_400_BAD_REQUEST)
