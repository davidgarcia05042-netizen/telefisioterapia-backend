"""
Editar y borrar dificultades (E5, docs/DECISIONES_TECNICAS.md):
GET/PUT/DELETE /exercises/{id}/difficulties/{difficulty_id}/.
"""
from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from telerehabilitation_API.therapy.models import (
    DifficultyRange, Exercise, ExerciseDifficulty, ExerciseSkeletonPointTracked, ScheduledTraining,
    ScheduledTrainingDifficulty,
)
from telerehabilitation_API.therapy.test_tracked_points import therapist_client


class DifficultyEditDeleteTests(TestCase):
    def setUp(self):
        self.api = therapist_client()
        self.exercise = Exercise.objects.create(name='Flexión de codo', description='', order=1)
        # Los SkeletonPoint 13 (codo) y 11 (hombro) los siembra la migración 0006.
        self.elbow = ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=13)
        self.shoulder = ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=11)
        self.easy = ExerciseDifficulty.objects.create(exercise=self.exercise, name='Fácil', description='Cómodo')
        DifficultyRange.objects.create(difficulty=self.easy, point_tracked=self.elbow, min_angle=50, max_angle=140)
        self.hard = ExerciseDifficulty.objects.create(exercise=self.exercise, name='Difícil', description='')
        DifficultyRange.objects.create(difficulty=self.hard, point_tracked=self.elbow, min_angle=30, max_angle=160)

    def url(self, difficulty=None, exercise=None):
        exercise_id = (exercise or self.exercise).id
        return f'/exercises/{exercise_id}/difficulties/{(difficulty or self.easy).id}/'

    def put(self, **overrides):
        body = {'name': 'Fácil (ajustada)', 'description': 'Nueva descripción',
                'ranges': [{'point_tracked_id': self.elbow.id, 'min_angle': 60, 'max_angle': 130},
                           {'point_tracked_id': self.shoulder.id, 'min_angle': 20, 'max_angle': 70}]}
        body.update(overrides)
        return self.api.put(self.url(), body, format='json')

    def assign(self, difficulty):
        scheduled = ScheduledTraining.objects.create(start_time=timezone.now())
        ScheduledTrainingDifficulty.objects.create(difficulty=difficulty, exercise=self.exercise,
                                                   scheduled_training=scheduled)

    def ranges_of(self, difficulty):
        return sorted(difficulty.ranges.values_list('point_tracked_id', 'min_angle', 'max_angle'))

    # --- Consultar ---

    def test_get_returns_the_difficulty_with_its_assignment_count(self):
        body = self.api.get(self.url()).json()
        self.assertEqual((body['name'], body['assigned_count']), ('Fácil', 0))
        self.assertEqual(body['ranges'][0]['point_tracked_id'], self.elbow.id)
        self.assign(self.easy)
        self.assertEqual(self.api.get(self.url()).json()['assigned_count'], 1)

    def test_a_difficulty_of_another_exercise_is_404(self):
        other = Exercise.objects.create(name='Otro', description='', order=1)
        self.assertEqual(self.api.get(self.url(exercise=other)).status_code, 404)
        self.assertEqual(self.api.delete(self.url(exercise=other)).status_code, 404)
        self.assertTrue(ExerciseDifficulty.objects.filter(pk=self.easy.pk).exists())

    # --- Editar ---

    def test_edit_changes_name_description_and_replaces_the_ranges(self):
        response = self.put()
        self.assertEqual(response.status_code, 200)
        self.easy.refresh_from_db()
        self.assertEqual((self.easy.name, self.easy.description), ('Fácil (ajustada)', 'Nueva descripción'))
        self.assertEqual(self.ranges_of(self.easy), sorted([(self.elbow.id, 60.0, 130.0), (self.shoulder.id, 20.0, 70.0)]))
        self.assertEqual(len(response.json()['ranges']), 2)
        # La otra dificultad no cambia.
        self.assertEqual(self.ranges_of(self.hard), [(self.elbow.id, 30.0, 160.0)])

    # --- Editar una dificultad asignada (F3, docs/DECISIONES_TECNICAS.md) ---

    def same_ranges(self):
        return [{'point_tracked_id': self.elbow.id, 'min_angle': 50, 'max_angle': 140}]

    def test_assigned_difficulty_can_change_name_and_description(self):
        self.assign(self.easy)
        response = self.put(name='Fácil (renombrada)', description='Otra', ranges=self.same_ranges())
        self.assertEqual(response.status_code, 200)
        self.easy.refresh_from_db()
        self.assertEqual((self.easy.name, self.easy.description), ('Fácil (renombrada)', 'Otra'))
        self.assertEqual(self.ranges_of(self.easy), [(self.elbow.id, 50.0, 140.0)])

    def test_assigned_difficulty_accepts_the_same_ranges_as_decimals(self):
        self.assign(self.easy)
        same = [{'point_tracked_id': self.elbow.id, 'min_angle': '50.00', 'max_angle': 140.0}]
        self.assertEqual(self.put(ranges=same).status_code, 200)

    def assert_range_change_rejected(self, ranges):
        response = self.put(name='No debe guardarse', ranges=ranges)
        self.assertEqual(response.status_code, 409)
        self.assertIn('No se pueden cambiar los rangos de la dificultad «Fácil»: está asignada en una rutina programada',
                      response.json()['detail'])
        self.assertIn('duplicarla como una nueva dificultad', response.json()['detail'])
        self.easy.refresh_from_db()
        self.assertEqual(self.easy.name, 'Fácil')
        self.assertEqual(self.ranges_of(self.easy), [(self.elbow.id, 50.0, 140.0)])

    def test_assigned_difficulty_cannot_change_an_angle(self):
        self.assign(self.easy)
        self.assert_range_change_rejected([{'point_tracked_id': self.elbow.id, 'min_angle': 55, 'max_angle': 140}])

    def test_assigned_difficulty_cannot_add_or_remove_a_range(self):
        self.assign(self.easy)
        self.assert_range_change_rejected(self.same_ranges() + [
            {'point_tracked_id': self.shoulder.id, 'min_angle': 20, 'max_angle': 70}])
        self.assert_range_change_rejected([])

    def test_the_results_keep_the_range_they_were_compared_with(self):
        self.assign(self.easy)
        self.assertEqual(self.put().status_code, 409)
        scheduled = ScheduledTraining.objects.get()
        diffs = self.api.get(f'/scheduled_training/diffs?scheduled_training_id={scheduled.id}'
                             f'&exercise_id={self.exercise.id}').json()
        self.assertEqual([(r['min_angle'], r['max_angle']) for r in diffs['difficulty']['ranges']], [(50.0, 140.0)])

    def test_invalid_data_is_400_before_the_conflict(self):
        self.assign(self.easy)
        self.assertEqual(self.put(name='').status_code, 400)

    def assert_rejected(self, response, text):
        self.assertEqual(response.status_code, 400)
        self.assertIn(text, response.json()['detail'])
        self.easy.refresh_from_db()
        self.assertEqual(self.easy.name, 'Fácil')
        self.assertEqual(self.ranges_of(self.easy), [(self.elbow.id, 50.0, 140.0)])

    def test_edit_without_name_is_400(self):
        self.assert_rejected(self.put(name='  '), 'debe tener un nombre')

    def test_edit_with_min_greater_than_max_is_400(self):
        self.assert_rejected(self.put(ranges=[{'point_tracked_id': self.elbow.id, 'min_angle': 150, 'max_angle': 40}]),
                             'En «Codo izquierdo» el ángulo mínimo es mayor que el máximo')

    def test_edit_with_a_joint_of_another_exercise_is_400(self):
        other = Exercise.objects.create(name='Otro', description='', order=1)
        foreign = ExerciseSkeletonPointTracked.objects.create(exercise=other, skeleton_point_id=25)
        self.assert_rejected(self.put(ranges=[{'point_tracked_id': foreign.id, 'min_angle': 1, 'max_angle': 2}]),
                             'no se rastrea en el ejercicio «Flexión de codo»')

    def test_edit_with_bad_angles_is_400(self):
        self.assert_rejected(self.put(ranges=[{'point_tracked_id': self.elbow.id, 'min_angle': 'a', 'max_angle': 2}]),
                             'debe ser un número')
        self.assert_rejected(self.put(ranges=[{'point_tracked_id': self.elbow.id, 'min_angle': 10, 'max_angle': 400}]),
                             'entre 0 y 360')
        self.assert_rejected(self.put(ranges=[{'point_tracked_id': self.elbow.id, 'max_angle': 20}]),
                             'debe ser un número')

    def test_edit_with_two_ranges_for_the_same_joint_is_400(self):
        entry = {'point_tracked_id': self.elbow.id, 'min_angle': 10, 'max_angle': 20}
        self.assert_rejected(self.put(ranges=[entry, entry]), 'tiene más de un rango')

    def test_edit_with_ranges_not_a_list_is_400(self):
        self.assert_rejected(self.put(ranges='[]'), 'debe ser una lista')

    # --- Borrar ---

    def test_delete_an_unassigned_difficulty_removes_it_and_its_ranges(self):
        range_ids = list(self.easy.ranges.values_list('id', flat=True))
        self.assertEqual(self.api.delete(self.url()).status_code, 204)
        self.assertFalse(ExerciseDifficulty.objects.filter(pk=self.easy.pk).exists())
        self.assertFalse(DifficultyRange.objects.filter(pk__in=range_ids).exists())
        self.assertTrue(ExerciseDifficulty.objects.filter(pk=self.hard.pk).exists())

    def test_delete_an_assigned_difficulty_is_409_and_deletes_nothing(self):
        self.assign(self.easy)
        self.assign(self.easy)
        response = self.api.delete(self.url())
        self.assertEqual(response.status_code, 409)
        self.assertIn('No se puede eliminar la dificultad «Fácil»: está asignada en 2 rutinas programadas',
                      response.json()['detail'])
        self.assertTrue(ExerciseDifficulty.objects.filter(pk=self.easy.pk).exists())
        self.assertEqual(self.ranges_of(self.easy), [(self.elbow.id, 50.0, 140.0)])

    def test_delete_message_for_a_single_assignment(self):
        self.assign(self.easy)
        self.assertIn('está asignada en una rutina programada', self.api.delete(self.url()).json()['detail'])

    # --- Permisos ---

    def test_requires_admin_or_therapist(self):
        self.assertEqual(APIClient().delete(self.url()).status_code, 401)
        user = User.objects.create_user('paciente-e5', password='x-pass-123')
        Group.objects.get_or_create(name='Patient')[0].user_set.add(user)
        patient = APIClient()
        patient.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=user).key}')
        self.assertEqual(patient.delete(self.url()).status_code, 403)
        self.assertEqual(patient.put(self.url(), {'name': 'x', 'ranges': []}, format='json').status_code, 403)
        self.assertTrue(ExerciseDifficulty.objects.filter(pk=self.easy.pk).exists())
