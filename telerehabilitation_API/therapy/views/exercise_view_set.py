import json

from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response

from telerehabilitation_API.authentication.permissions import IsAdminOrTherapist
from telerehabilitation_API.processing import client as processing_client
from telerehabilitation_API.processing.callbacks import handle_callback
from telerehabilitation_API.processing.results import REFERENCE, Target
from telerehabilitation_API.processing.stale import expire_stale_processing

from telerehabilitation_API.therapy.models import Exercise, ExerciseSkeletonPointTracked, ExerciseDifficulty, \
    DifficultyRange
from telerehabilitation_API.therapy.permissions import CanCreateExercise, CanEditExercise, CanDeleteExercise
from telerehabilitation_API.therapy.querysets import exercise_lookups
from telerehabilitation_API.therapy.serializers import ExerciseSerializer, SkeletonPointSerializer
from telerehabilitation_API.therapy.serializers.exercise_difficulty_serializer import ExerciseDifficultySerializer
from telerehabilitation_API.therapy.serializers.exercise_video_serializer import ExerciseVideoSerializer
from telerehabilitation_API.therapy.tracked_points import parse_points, sync_tracked_points
from telerehabilitation_API.therapy.difficulties import (
    DifficultyError, assignment_count, delete_difficulty, parse_update, update_conflict, update_difficulty,
)


class ExerciseViewSet(viewsets.ModelViewSet):
    queryset = Exercise.objects.all()
    serializer_class = ExerciseSerializer

    def get_queryset(self):
        # Sin el movimiento (lo aplaza el manager) y con lo que anida el
        # serializer en una consulta por nivel, no una por ejercicio.
        queryset = super().get_queryset()
        if self.action in ('list', 'retrieve'):
            queryset = queryset.prefetch_related(*exercise_lookups())
        return queryset

    # Al leer, una referencia "procesando" sin callback hace más de 15 min pasa
    # a error (processing/stale.py): el panel consulta estas dos rutas.
    def list(self, request, *args, **kwargs):
        expire_stale_processing()
        return super().list(request, *args, **kwargs)

    def retrieve(self, request, *args, **kwargs):
        expire_stale_processing()
        return super().retrieve(request, *args, **kwargs)

    def create(self, request, *args, **kwargs):
        exercise_serialized = self.get_serializer(data=request.data)
        exercise_serialized.is_valid(raise_exception=True)
        self.perform_create(exercise_serialized)
        headers = self.get_success_headers(exercise_serialized.data)
        return Response(exercise_serialized.data, status=status.HTTP_201_CREATED, headers=headers)

    def get_permissions(self):
        # El callback del servicio no usa token: lo autentica la firma HMAC.
        if self.action == 'video_results':
            return [permissions.AllowAny()]
        # El movimiento medido (varios MB, datos del paciente de referencia):
        # solo administradores y terapeutas.
        if self.action in ('movement', 'difficulty'):
            return [permissions.IsAuthenticated(), IsAdminOrTherapist()]
        # M1 (docs/DECISIONES_TECNICAS.md): solo el panel (Admin o Therapist); un paciente con
        # token leía y modificaba terapias, rutinas y ejercicios.
        permissions_classes = [permissions.IsAuthenticated, IsAdminOrTherapist]
        if self.action == 'create':
            permissions_classes.append(CanCreateExercise)
        elif self.action in ['update', 'partial_update']:
            permissions_classes.append(CanEditExercise)
        elif self.action == 'destroy':
            permissions_classes.append(CanDeleteExercise)
        return [permission() for permission in permissions_classes]

    @action(methods=['post'], detail=True)
    def video(self, request, pk=None):
        exercise = self.get_object()
        video_serialized = ExerciseVideoSerializer(data=request.data)
        errors = []
        if not video_serialized.is_valid():
            errors.append(video_serialized.errors)
        if 'points' not in request.data:
            errors.append({ 'points': 'There is no points' })
        if not errors:
            # Antes de borrar nada: sin servicio configurado, el video nunca se
            # mediría y el ejercicio quedaría "procesando" para siempre.
            missing = processing_client.missing_settings()
            if missing:
                return Response(
                    {'error': f'Servicio de procesamiento no configurado: falta {", ".join(missing)}.'},
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            # Atómico: si algo falla al actualizar las articulaciones no queda un
            # ejercicio a medias, y el envío solo arranca tras el commit.
            with transaction.atomic():
                # En sitio por skeleton_point_id: borrar y recrear dejaba
                # huérfanos los rangos de las dificultades (SET_NULL).
                sync_tracked_points(exercise, parse_points(request.data['points']))

                exercise.video = video_serialized.validated_data['video']
                exercise.status = Exercise.PROCESSING
                exercise.processing_started_at = timezone.now()
                # El movimiento era del video anterior: hasta que llegue el
                # callback del nuevo, el panel no debe mostrarlo como actual.
                exercise.movement = None
                exercise.save()
                processing_client.dispatch_after_commit(Target(REFERENCE, exercise.id), exercise.video)
            return Response({}, status=200)
        return Response({'errors': errors}, status=400)

    @action(methods=['post'], detail=True, authentication_classes=[])
    def video_results(self, request, pk=None):
        """
        Callback del Servicio de Procesamiento para el video de referencia:
        POST /exercises/{id}/video_results/?point_tracked={pt_id}

        Sin token (la única credencial es la firma HMAC) y sin get_object():
        el 404 lo decide el manejador, después de verificar la firma.
        """
        return handle_callback(request, REFERENCE, pk)

    @action(methods=['get'], detail=True)
    def movement(self, request, pk=None):
        """
        GET /exercises/{id}/movement/: movimiento medido por el servicio
        (docs/DECISIONES_TECNICAS.md, D4), con la estructura del panel. 404 si
        todavía no hay (sin video, procesando o con error).
        """
        exercise = self.get_object()
        # El único endpoint que entrega el movimiento: se carga solo aquí.
        movement = Exercise.objects.filter(pk=exercise.pk).values_list('movement', flat=True).first()
        if movement is None:
            return Response({'detail': 'Sin movimiento medido por el servicio.'}, status=404)
        return Response(movement)

    @action(methods=['get'], detail=True)
    def points_tracked(self, request, pk=None):
        exercise = self.get_object()
        points = []
        for point in exercise.tracked_points.select_related('skeleton_point', 'left_point', 'right_point').all():
            point_serialized = SkeletonPointSerializer(point.skeleton_point).data
            point_serialized.update({
                'max_angle': point.max_angle,
                'min_angle': point.min_angle,
                'point_id': point.id,
                # Resolver adyacentes: personalizado del ejercicio > por defecto del esqueleto
                'left_point': point.left_point_id if point.left_point_id is not None else (point.skeleton_point.left_point_id if point.skeleton_point else None),
                'right_point': point.right_point_id if point.right_point_id is not None else (point.skeleton_point.right_point_id if point.skeleton_point else None),
            })
            points.append(point_serialized)
        return Response(points)

    @action(methods=['get'], detail=True)
    def difficulties(self, request, pk=None):
        exercise = self.get_object()
        return Response(
            ExerciseDifficultySerializer(exercise.difficulties, many=True).data
        )

    @action(methods=['get', 'put', 'delete'], detail=True, url_path=r'difficulties/(?P<difficulty_id>\d+)')
    def difficulty(self, request, pk=None, difficulty_id=None):
        """
        Una dificultad del ejercicio (E5, docs/DECISIONES_TECNICAS.md; therapy/difficulties.py).
        GET la devuelve con `assigned_count` (rutinas programadas que la usan),
        PUT la edita (nombre, descripción y rangos; si está asignada, los
        rangos no pueden cambiar: 409) y DELETE la borra si no está asignada
        (409 con el motivo si lo está).
        """
        exercise = self.get_object()
        difficulty = exercise.difficulties.filter(pk=difficulty_id).first()
        if difficulty is None:
            return Response({'detail': 'La dificultad no existe en este ejercicio.'}, status=404)
        if request.method == 'DELETE':
            conflict = delete_difficulty(difficulty)
            if conflict:
                return Response({'detail': conflict}, status=409)
            return Response(status=204)
        if request.method == 'PUT':
            try:
                data = parse_update(exercise, request.data)
            except DifficultyError as error:
                return Response({'detail': str(error)}, status=400)
            conflict = update_conflict(difficulty, data)
            if conflict:
                return Response({'detail': conflict}, status=409)
            update_difficulty(difficulty, data)
            difficulty.refresh_from_db()
        return Response({**ExerciseDifficultySerializer(difficulty).data,
                         'assigned_count': assignment_count(difficulty)})

    @action(methods=['post'], detail=True)
    def post_difficulty(self, request, pk=None):
        exercise = self.get_object()
        if 'name' not in request.data or 'description' not in request.data or 'ranges' not in request.data:
            return Response(status=400)
        ranges = json.loads(request.data['ranges'])
        difficulty = ExerciseDifficulty.objects.create(
            # El ejercicio de la URL (el original usaba `exercise_id` del cuerpo,
            # que podía ser otro ejercicio).
            exercise=exercise,
            name=request.data['name'],
            description=request.data['description']
        )
        for range in ranges:
            DifficultyRange.objects.create(
                point_tracked_id=int(range['point_tracked']['point_id']),
                difficulty_id=difficulty.id,
                max_angle=range['max_angle'],
                min_angle=range['min_angle'],
            )
        return Response(status=201)