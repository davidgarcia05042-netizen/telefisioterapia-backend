import json

import pytz
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from telerehabilitation_API.authentication.permissions import (
    IsAdminOrTherapist, IsAdminTherapistOrPatient, is_staff_member,
)
from telerehabilitation_API.processing import client as processing_client
from telerehabilitation_API.processing.callbacks import handle_callback
from telerehabilitation_API.processing.results import PATIENT, Target
from telerehabilitation_API.processing.stale import expire_stale_processing
from telerehabilitation_API.therapy.models import Exercise, ExerciseResult, Training
from telerehabilitation_API.therapy.ownership import as_id, exercise_results_for, trainings_for
from telerehabilitation_API.therapy.querysets import with_result_details
from telerehabilitation_API.therapy.serializers.exercise_results_serializer import ExerciseResultSerializer
from telerehabilitation_API.therapy.serializers.exercise_results_video_serializer import ExerciseResultsVideoSerializer
from drf_yasg.utils import swagger_auto_schema


class ExerciseResultViewSet(APIView):
    """
    Resultados del paciente (M1, docs/DECISIONES_TECNICAS.md). Antes el GET no exigía
    autenticación: devolvía todos los resultados con la URL del video.

    - GET ?training_id= / ?exercise_result_id=: el panel, o el paciente si la
      sesión o el resultado son suyos (si no, 404).
    - GET sin parámetros (todos los resultados): solo el panel.
    - POST: el panel, o el paciente sobre una sesión suya y un ejercicio de
      esa rutina.
    """
    permission_classes = [IsAuthenticated, IsAdminTherapistOrPatient]

    @swagger_auto_schema(
        operation_description="",
        operation_summary="",
        responses={200: ExerciseResultSerializer}
    )
    def get(self, request):
        # Al leer, un resultado "procesando" sin callback hace más de 15 min pasa
        # a error (processing/stale.py): la app y el panel consultan esta ruta.
        expire_stale_processing()
        exercise_result_id = request.GET.get('exercise_result_id', 'NOT_FOUND')
        training_id = request.GET.get('training_id', 'NOT_FOUND')
        results = with_result_details(exercise_results_for(request.user))
        if exercise_result_id != 'NOT_FOUND':
            try:
                return Response(
                    ExerciseResultSerializer(
                        results.get(pk=as_id(exercise_result_id)),
                        context={'request': request}
                    ).data,
                    status=status.HTTP_200_OK
                )
            except ExerciseResult.DoesNotExist:
                return Response({}, status=status.HTTP_404_NOT_FOUND)

        if training_id != 'NOT_FOUND':
            # Una sesión ajena es 404, no una lista vacía: no se revela que existe.
            if not trainings_for(request.user).filter(pk=as_id(training_id)).exists():
                return Response({}, status=status.HTTP_404_NOT_FOUND)
            try:
                return Response(
                    ExerciseResultSerializer(
                        results.filter(training_id=as_id(training_id)).all(),
                        many=True,
                        context={'request': request}
                    ).data,
                    status=status.HTTP_200_OK
                )
            except ExerciseResult.DoesNotExist:
                return Response({}, status=status.HTTP_404_NOT_FOUND)

        if not is_staff_member(request.user):
            return Response({'detail': 'No tienes permiso para realizar esta acción.'}, status=status.HTTP_403_FORBIDDEN)
        try:
            return Response(
                ExerciseResultSerializer(
                    with_result_details(ExerciseResult.objects.all()),
                    many=True,
                    context={'request': request}
                ).data,
                status=status.HTTP_200_OK
            )
        except ExerciseResult.DoesNotExist:
            return Response({}, status=status.HTTP_404_NOT_FOUND)
    @swagger_auto_schema(
        operation_summary="Upload a new video URL",
        operation_description="This end point handles the request for the video URL.",
        request_body=ExerciseResultsVideoSerializer,
        responses={200: 'OK'}
    )
    def post(self, request):
        missing = processing_client.missing_settings()
        if missing:
            return Response(
                {'error': f'Servicio de procesamiento no configurado: falta {", ".join(missing)}.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        video_serialized = ExerciseResultsVideoSerializer(data=request.data)
        errors = []
        if not video_serialized.is_valid():
            errors.append(video_serialized.errors)
        training = None
        if 'training_id' not in request.data:
            errors.append({'training_id': 'There is no training_id'})
        else:
            training = Training.objects.filter(pk=as_id(request.data['training_id'])).select_related(
                'schedule_training').first()
            if training is None:
                errors.append({'training_id': 'Training not found'})
            elif not trainings_for(request.user).filter(pk=training.pk).exists():
                # Sesión de otro paciente: 404, como si no existiera.
                return Response({}, status=status.HTTP_404_NOT_FOUND)
        if 'exercise_id' not in request.data:
            errors.append({'exercise_id': 'There is no exercise_id'})
        elif not Exercise.objects.filter(pk=as_id(request.data['exercise_id'])).exists():
            errors.append({'exercise_id': 'Exercise not found'})
        elif training is not None and not is_staff_member(request.user) and not Exercise.objects.filter(
                pk=as_id(request.data['exercise_id']),
                routine_id=training.schedule_training.routine_id).exists():
            # La app solo sube videos de los ejercicios de su rutina.
            errors.append({'exercise_id': 'El ejercicio no pertenece a la rutina de esta sesión.'})
        if not errors:
            # El envío al servicio se programa para después del commit: el hilo
            # debe encontrar el resultado ya guardado.
            with transaction.atomic():
                new_exercise_result = ExerciseResult.objects.create(
                    exercise_id=request.data['exercise_id'],
                    training_id=request.data['training_id'],
                    start_time=timezone.localtime(timezone.now(), pytz.timezone('America/Bogota')),
                    video=video_serialized.validated_data['video'],
                    status=ExerciseResult.PROCESSING,
                )
                processing_client.dispatch_after_commit(
                    Target(PATIENT, new_exercise_result.id), new_exercise_result.video,
                )
            return Response({'id': new_exercise_result.id}, status=status.HTTP_201_CREATED)
        return Response({'errors': errors}, status=status.HTTP_400_BAD_REQUEST)



class ExerciseResultResultsViewSet(APIView):
    """
    Callback del Servicio de Procesamiento para el video del paciente:
    POST /exercise_results/{id}/results?point_tracked={pt_id}

    Sin autenticación de DRF: la única credencial válida es la firma HMAC del
    servicio. En la plataforma original este endpoint aceptaba escrituras de
    cualquiera.
    """
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request, exercise_result_id):
        return handle_callback(request, PATIENT, exercise_result_id)


class ExerciseResultMovementViewSet(APIView):
    """
    GET /exercise_results/{id}/movement: movimiento del paciente medido por el
    servicio (docs/DECISIONES_TECNICAS.md, D4). Va aparte de los listados porque
    pesa varios MB. 404 si todavía no hay.
    """
    permission_classes = [IsAuthenticated, IsAdminOrTherapist]

    def get(self, request, exercise_result_id):
        result = ExerciseResult.objects.filter(pk=exercise_result_id).only('movement').first()
        if result is None or result.movement is None:
            return Response({'detail': 'Sin movimiento medido por el servicio.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(result.movement)


class ExerciseResultConceptViewSet(APIView):
    # Concepto clínico del terapeuta: solo el panel. Antes no exigía autenticación.
    permission_classes = [IsAuthenticated, IsAdminOrTherapist]

    def post(self, request, exercise_result_id):
        if 'concept' not in request.data:
            return Response({}, status=status.HTTP_400_BAD_REQUEST)
        try:
            exercise_result = ExerciseResult.objects.get(pk=exercise_result_id)
            exercise_result.concept = request.data['concept']
            exercise_result.save()
            return Response(status=201)
        except ExerciseResult.DoesNotExist:
            return Response({}, status=status.HTTP_400_BAD_REQUEST)
