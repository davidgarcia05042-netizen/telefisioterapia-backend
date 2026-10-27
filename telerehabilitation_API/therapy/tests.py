import importlib

from django.apps import apps
from django.conf import settings
from django.contrib.auth.models import Group
from django.core.management import call_command
from django.test import TestCase

from telerehabilitation_API.therapy.models import (
    Exercise, ExerciseResult, ExerciseSkeletonPointTracked, SkeletonPoint,
)
from telerehabilitation_API.therapy.models.exercise_result_point import ExerciseResultPoint
from telerehabilitation_API.therapy.serializers.exercise_results_point_serializer import (
    ExerciseResultPointSerializer,
)

# El nombre del módulo empieza por dígito: no se puede importar con `import`.
seed_migration = importlib.import_module(
    'telerehabilitation_API.therapy.migrations.0006_seed_skeleton_points'
)

EXPECTED_NEIGHBORS = {
    11: (13, 23), 12: (14, 24), 13: (11, 15), 14: (12, 16),
    15: (13, 19), 16: (14, 20), 23: (11, 25), 24: (12, 26),
    25: (23, 27), 26: (24, 28), 27: (25, 31), 28: (26, 32),
}


class SkeletonSeedTests(TestCase):
    """La base de pruebas ya corrió todas las migraciones, incluida la 0006."""

    def assert_seeded(self):
        self.assertEqual(SkeletonPoint.objects.count(), 33)
        self.assertEqual(
            sorted(SkeletonPoint.objects.values_list('id', flat=True)), list(range(33)),
        )
        with_neighbors = {
            p.id: (p.left_point_id, p.right_point_id)
            for p in SkeletonPoint.objects.exclude(left_point=None)
        }
        self.assertEqual(with_neighbors, EXPECTED_NEIGHBORS)
        self.assertFalse(SkeletonPoint.objects.exclude(left_point=None, right_point=None)
                         .exclude(id__in=EXPECTED_NEIGHBORS).exists())

    def test_ids_are_mediapipe_indices(self):
        self.assert_seeded()
        elbow = SkeletonPoint.objects.get(id=13)
        self.assertEqual((elbow.codename, elbow.verbose), ('LEFT_ELBOW', 'Codo izquierdo'))
        self.assertEqual(SkeletonPoint.objects.get(id=0).codename, 'NOSE')
        self.assertEqual(SkeletonPoint.objects.get(id=32).codename, 'RIGHT_FOOT_INDEX')

    def test_seed_is_idempotent(self):
        seed_migration.seed_skeleton_points(apps, None)
        seed_migration.seed_skeleton_points(apps, None)
        self.assert_seeded()

    def test_seed_restores_altered_rows(self):
        SkeletonPoint.objects.filter(id=13).update(codename='X', left_point=None)
        seed_migration.seed_skeleton_points(apps, None)
        self.assert_seeded()
        self.assertEqual(SkeletonPoint.objects.get(id=13).codename, 'LEFT_ELBOW')


class InitialDataFixtureTests(TestCase):
    """
    initial_data.json asigna permisos por ID numérico. Esta prueba detecta si
    algún cambio de modelos desplaza esos IDs y deja los grupos mal armados.
    """

    def setUp(self):
        call_command('loaddata', settings.BASE_DIR / 'initial_data.json', verbosity=0)

    def permissions_of(self, group_name):
        group = Group.objects.get(name=group_name)
        return {f'{p.content_type.app_label}.{p.codename}' for p in group.permissions.all()}

    def test_therapist_group(self):
        perms = self.permissions_of('Therapist')
        self.assertEqual(len(perms), 32)
        self.assertIn('therapy.add_exercise', perms)

    def test_patient_group(self):
        self.assertEqual(len(self.permissions_of('Patient')), 8)


class ExerciseResultPointSerializerTests(TestCase):
    def test_exposes_angle_series_and_error(self):
        exercise = Exercise.objects.create(name='Flexión de codo', description='', order=1)
        tracked = ExerciseSkeletonPointTracked.objects.create(
            exercise=exercise, skeleton_point_id=13,
        )
        result = ExerciseResult.objects.create(exercise=exercise)
        point = ExerciseResultPoint.objects.create(
            exercise_result=result, point_tracked=tracked,
            max_angle=145.2, min_angle=31.7,
            angle_series=[{'t_ms': 0.0, 'angle': 92.4}, {'t_ms': 33.3, 'angle': None}],
            error=None,
        )
        data = ExerciseResultPointSerializer(point).data
        self.assertEqual(data['angle_series'][1], {'t_ms': 33.3, 'angle': None})
        self.assertIsNone(data['error'])
        self.assertEqual(data['max_angle'], 145.2)
