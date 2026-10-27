from rest_framework import permissions, viewsets

from telerehabilitation_API.authentication.permissions import IsAdminOrTherapist
from telerehabilitation_API.therapy.models import Routine
from telerehabilitation_API.therapy.permissions import CanCreateRoutine, CanEditRoutine, CanDeleteRoutine
from telerehabilitation_API.therapy.querysets import with_routine_details
from telerehabilitation_API.therapy.serializers import RoutineSerializer


class RoutineViewSet(viewsets.ModelViewSet):
    queryset = Routine.objects.all()
    serializer_class = RoutineSerializer

    def get_queryset(self):
        # Los ejercicios anidados sin su movimiento y sin una consulta por ejercicio.
        queryset = super().get_queryset()
        if self.action in ('list', 'retrieve'):
            queryset = with_routine_details(queryset)
        return queryset

    def get_permissions(self):
        # M1 (docs/DECISIONES_TECNICAS.md): solo el panel (Admin o Therapist); un paciente con
        # token leía y modificaba terapias, rutinas y ejercicios.
        permissions_classes = [permissions.IsAuthenticated, IsAdminOrTherapist]
        if self.action == 'create':
            permissions_classes.append(CanCreateRoutine)
        elif self.action in ['update', 'partial_update']:
            permissions_classes.append(CanEditRoutine)
        elif self.action == 'destroy':
            permissions_classes.append(CanDeleteRoutine)
        return [permission() for permission in permissions_classes]
