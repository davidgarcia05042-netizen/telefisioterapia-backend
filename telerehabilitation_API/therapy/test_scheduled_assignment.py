"""
Asignar una rutina (POST /scheduled_training): validación de las dificultades
y creación dentro de una transacción. Y /scheduled_training/diffs, que ahora
exige Admin o Therapist (hallazgos de la plataforma original, docs/DECISIONES_TECNICAS.md).
"""
from unittest import mock

from django.contrib.auth.models import Group, User
from django.test import TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from telerehabilitation_API.authentication.models import Patient
from telerehabilitation_API.therapy.models import (
    Exercise, ExerciseDifficulty, Routine, ScheduledTraining, ScheduledTrainingDifficulty, Therapy, TherapyPatient,
)
from telerehabilitation_API.therapy.test_tracked_points import therapist_client

START_TIME = '20/10/2026 09:30:00'


class AssignRoutineTests(TestCase):
    def setUp(self):
        self.api = therapist_client()
        therapy = Therapy.objects.create(name='Rodilla', description='')
        self.routine = Routine.objects.create(therapy=therapy, name='Rutina A', description='')
        self.elbow = Exercise.objects.create(routine=self.routine, name='Flexión de codo', description='', order=1)
        self.squat = Exercise.objects.create(routine=self.routine, name='Sentadilla', description='', order=2)
        self.easy = ExerciseDifficulty.objects.create(exercise=self.elbow, name='Fácil', description='')
        self.medium = ExerciseDifficulty.objects.create(exercise=self.squat, name='Media', description='')
        self.therapy_patient = TherapyPatient.objects.create(therapy=therapy)

    def post(self, **overrides):
        body = {'routine': self.routine.id, 'therapy_patient': self.therapy_patient.id,
                'start_time': START_TIME, 'difficulties': []}
        body.update(overrides)
        return self.api.post('/scheduled_training', body, format='json')

    def assert_rejected(self, response, text):
        self.assertEqual(response.status_code, 400)
        self.assertIn(text, response.json()['detail'])
        self.assertEqual(ScheduledTraining.objects.count(), 0)
        self.assertEqual(ScheduledTrainingDifficulty.objects.count(), 0)

    # --- Casos válidos ---

    def test_creates_the_assignment_with_its_difficulties(self):
        response = self.post(difficulties=[{'exercise_id': self.elbow.id, 'diff': self.easy.id},
                                           {'exercise_id': self.squat.id, 'diff': self.medium.id}])
        self.assertEqual(response.status_code, 201)
        scheduled = ScheduledTraining.objects.get()
        self.assertEqual((scheduled.routine, scheduled.therapy_patient), (self.routine, self.therapy_patient))
        self.assertEqual(timezone.localtime(scheduled.start_time, timezone.get_fixed_timezone(-300)).strftime(
            '%d/%m/%Y %H:%M:%S'), START_TIME)
        self.assertEqual(
            set(ScheduledTrainingDifficulty.objects.values_list('exercise_id', 'difficulty_id')),
            {(self.elbow.id, self.easy.id), (self.squat.id, self.medium.id)},
        )

    def test_difficulties_are_optional(self):
        # T3: los ejercicios sin dificultad no se envían.
        self.assertEqual(self.post(difficulties=[{'exercise_id': self.elbow.id, 'diff': self.easy.id}]).status_code, 201)
        self.assertEqual(ScheduledTrainingDifficulty.objects.count(), 1)
        self.assertEqual(self.post(difficulties=[]).status_code, 201)
        body = {'routine': self.routine.id, 'therapy_patient': self.therapy_patient.id, 'start_time': START_TIME}
        self.assertEqual(self.api.post('/scheduled_training', body, format='json').status_code, 201)

    # --- Dificultades inválidas: 400 y nada creado ---

    def test_missing_diff_is_400_instead_of_500(self):
        self.assert_rejected(self.post(difficulties=[{'exercise_id': self.elbow.id}]),
                             'Falta la dificultad ("diff") del ejercicio «Flexión de codo»')

    def test_difficulty_of_another_exercise_is_400(self):
        self.assert_rejected(self.post(difficulties=[{'exercise_id': self.elbow.id, 'diff': self.medium.id}]),
                             f'La dificultad {self.medium.id} no pertenece al ejercicio «Flexión de codo»')

    def test_unknown_difficulty_is_400(self):
        self.assert_rejected(self.post(difficulties=[{'exercise_id': self.elbow.id, 'diff': 9999}]),
                             'no pertenece al ejercicio')

    def test_exercise_outside_the_routine_is_400(self):
        other = Exercise.objects.create(name='Otro', description='', order=1)
        difficulty = ExerciseDifficulty.objects.create(exercise=other, name='Fácil', description='')
        self.assert_rejected(self.post(difficulties=[{'exercise_id': other.id, 'diff': difficulty.id}]),
                             'no pertenece a la rutina «Rutina A»')

    def test_missing_exercise_id_is_400(self):
        self.assert_rejected(self.post(difficulties=[{'diff': self.easy.id}]), '"exercise_id"')

    def test_two_difficulties_for_the_same_exercise_is_400(self):
        entry = {'exercise_id': self.elbow.id, 'diff': self.easy.id}
        self.assert_rejected(self.post(difficulties=[entry, entry]), 'tiene más de una dificultad')

    def test_a_bad_entry_after_a_good_one_creates_nothing(self):
        self.assert_rejected(self.post(difficulties=[{'exercise_id': self.elbow.id, 'diff': self.easy.id},
                                                     {'exercise_id': self.squat.id}]), 'Falta la dificultad')

    def test_difficulties_must_be_a_list(self):
        self.assert_rejected(self.post(difficulties={'exercise_id': self.elbow.id}), 'debe ser una lista')

    # --- Resto del cuerpo ---

    def test_bad_start_time_is_400_instead_of_500(self):
        self.assert_rejected(self.post(start_time='2026-10-20T09:30'), 'DD/MM/AAAA HH:MM:SS')

    def test_unknown_routine_or_patient_is_400(self):
        self.assert_rejected(self.post(routine=9999), 'La rutina no existe')
        self.assert_rejected(self.post(therapy_patient=9999), 'no está inscrito')

    # --- Transacción ---

    def test_a_failure_while_creating_rolls_everything_back(self):
        with mock.patch.object(ScheduledTrainingDifficulty.objects, 'bulk_create',
                               side_effect=RuntimeError('falla simulada')):
            with self.assertRaises(RuntimeError):
                self.post(difficulties=[{'exercise_id': self.elbow.id, 'diff': self.easy.id}])
        self.assertEqual(ScheduledTraining.objects.count(), 0)


class DiffsAuthenticationTests(TestCase):
    """Admin y Therapist leen cualquiera; el paciente, solo las de sus rutinas (M1, docs/DECISIONES_TECNICAS.md)."""

    def setUp(self):
        therapy = Therapy.objects.create(name='Rodilla', description='')
        self.owner, owner_patient = self.patient('dueno')
        _, self.other_patient = self.patient('otro')
        tp = TherapyPatient.objects.create(therapy=therapy, patient=owner_patient)
        self.scheduled = ScheduledTraining.objects.create(therapy_patient=tp, start_time=timezone.now())
        self.url = f'/scheduled_training/diffs?scheduled_training_id={self.scheduled.id}&exercise_id=1'

    def patient(self, name):
        user = User.objects.create_user(f'paciente-{name}', password='x-pass-123')
        Group.objects.get_or_create(name='Patient')[0].user_set.add(user)
        return user, Patient.objects.create(user=user)

    def client_for(self, user):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=user).key}')
        return api

    def client_in_group(self, group):
        user = User.objects.create_user(f'usuario-{group}', password='x-pass-123')
        Group.objects.get_or_create(name=group)[0].user_set.add(user)
        return self.client_for(user)

    def test_without_token_is_401(self):
        self.assertEqual(APIClient().get(self.url).status_code, 401)

    def test_the_patient_reads_their_own(self):
        response = self.client_for(self.owner).get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'difficulty': None})

    def test_another_patient_gets_404(self):
        self.assertEqual(self.client_for(self.other_patient.user).get(self.url).status_code, 404)

    def test_a_user_without_role_is_403(self):
        user = User.objects.create_user('sin-rol', password='x-pass-123')
        self.assertEqual(self.client_for(user).get(self.url).status_code, 403)

    def test_admin_and_therapist_can_read(self):
        for group in ('Admin', 'Therapist'):
            response = self.client_in_group(group).get(self.url)
            self.assertEqual(response.status_code, 200, group)
            self.assertEqual(response.json(), {'difficulty': None})
