import json

from rest_framework import viewsets, permissions
from rest_framework.decorators import action
from rest_framework.response import Response

from telerehabilitation_API.authentication.permissions import IsAdminOrTherapist
from telerehabilitation_API.therapy.models import Therapy, TherapyPatient
from telerehabilitation_API.therapy.permissions import CanCreateTherapy, CanEditTherapy, CanDeleteTherapy
from telerehabilitation_API.therapy.querysets import with_routine_details
from telerehabilitation_API.therapy.serializers import TherapySerializer, RoutineSerializer
from telerehabilitation_API.authentication.serializers import PatientSerializer


class TherapyViewSet(viewsets.ModelViewSet):
    queryset = Therapy.objects.all()
    serializer_class = TherapySerializer

    def get_queryset(self):
        # Los enlaces a las rutinas de cada terapia, en una sola consulta.
        queryset = super().get_queryset()
        if self.action in ('list', 'retrieve'):
            queryset = queryset.prefetch_related('routines')
        return queryset

    def get_permissions(self):
        # M1 (docs/DECISIONES_TECNICAS.md): solo el panel (Admin o Therapist); un paciente con
        # token leía y modificaba terapias, rutinas y ejercicios.
        permissions_classes = [permissions.IsAuthenticated, IsAdminOrTherapist]
        if self.action == 'create':
            permissions_classes.append(CanCreateTherapy)
        elif self.action in ['update', 'partial_update']:
            permissions_classes.append(CanEditTherapy)
        elif self.action == 'destroy':
            permissions_classes.append(CanDeleteTherapy)
        return [permission() for permission in permissions_classes]

    @action(methods=['get'], detail=True)
    def get_patients(self, request, pk=None):
        return Response(
            [{
                'id': x.id,
                'patient': PatientSerializer(x.patient).data
            } for x in self.get_object().patients.all()
            ]
        )

    @action(methods=['post'], detail=True)
    def enroll_patient(self, request, pk=None):
        therapy = self.get_object()
        body = request.data
        TherapyPatient.objects.create(
            therapist_id=body['therapist'],
            therapy_id=body['therapy'],
            patient_id=body['patient']
        )
        return Response(body)

    @action(methods=['get'], detail=True)
    def routines(self, request, pk=None):
        therapy = self.get_object()
        routines = with_routine_details(therapy.routines.all())
        return Response(RoutineSerializer(routines, many=True, context={'request': request}).data)
