"""
Regresión del OOM del 2026-10-23: ningún listado carga el movimiento y el
número de consultas no crece con los ejercicios (models/managers.py y
querysets.py).
"""
from contextlib import contextmanager

from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.conf import settings
from django.db import connection
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from telerehabilitation_API.authentication.models import Patient, Therapist
from telerehabilitation_API.processing.test_callbacks_v2 import STANDARD, V2CallbackTestCase
from telerehabilitation_API.therapy.models import (
    DifficultyRange, Exercise, ExerciseDifficulty, ExerciseResult, ExerciseSkeletonPointTracked, Routine,
    ScheduledTraining, ScheduledTrainingDifficulty, Therapy, TherapyPatient, Training,
)
from telerehabilitation_API.therapy.models.exercise_result_point import ExerciseResultPoint

MOVEMENT = {'frames': [{'t': 0.0, 'points': [[0.5, 0.5, 0.0, 1.0]] * 33}]}


@contextmanager
def capture_sql():
    """SQL ejecutado dentro del bloque, contado con un execute_wrapper."""
    sqls = []

    def wrapper(execute, sql, params, many, context):
        sqls.append(sql)
        return execute(sql, params, many, context)

    with connection.execute_wrapper(wrapper):
        yield sqls


def selects_with_movement(sqls):
    return [sql for sql in sqls if sql.lstrip().upper().startswith('SELECT') and '"movement"' in sql]


class ListingsDoNotLoadMovementTests(TestCase):
    def setUp(self):
        call_command('loaddata', settings.BASE_DIR / 'initial_data.json', verbosity=0)
        therapist_user = User.objects.create_user('tera', password='x-pass-123')
        Group.objects.get(name='Therapist').user_set.add(therapist_user)
        therapist = Therapist.objects.create(user=therapist_user)
        self.patient_user = User.objects.create_user('paciente', password='x-pass-123')
        Group.objects.get(name='Patient').user_set.add(self.patient_user)
        patient = Patient.objects.create(user=self.patient_user)

        self.therapy = Therapy.objects.create(name='Rodilla', description='')
        self.routine = Routine.objects.create(therapy=self.therapy, name='Rutina', description='')
        self.tp = TherapyPatient.objects.create(therapy=self.therapy, therapist=therapist, patient=patient)
        self.schedule = ScheduledTraining.objects.create(routine=self.routine, therapy_patient=self.tp,
                                                         start_time=timezone.now())
        self.training = Training.objects.create(schedule_training=self.schedule, start_time=timezone.now())
        for _ in range(2):
            self.add_exercise()

        self.staff = APIClient()
        self.staff.force_authenticate(therapist_user)
        self.patient = APIClient()
        self.patient.force_authenticate(self.patient_user)

    def add_exercise(self):
        """Un ejercicio procesado con todo lo que anidan los serializers, y su resultado."""
        n = Exercise.objects.count()
        exercise = Exercise.objects.create(routine=self.routine, name=f'Ejercicio {n}', description='', order=n,
                                           status=Exercise.PROCESSED, movement=MOVEMENT)
        difficulty = ExerciseDifficulty.objects.create(exercise=exercise, name='Media', description='')
        for skeleton_point in (13, 14):
            tracked = ExerciseSkeletonPointTracked.objects.create(exercise=exercise, skeleton_point_id=skeleton_point)
            DifficultyRange.objects.create(difficulty=difficulty, point_tracked=tracked, min_angle=10, max_angle=90)
        ScheduledTrainingDifficulty.objects.create(difficulty=difficulty, exercise=exercise,
                                                   scheduled_training=self.schedule)
        result = ExerciseResult.objects.create(exercise=exercise, training=self.training,
                                               status=ExerciseResult.PROCESSED, movement=MOVEMENT)
        ExerciseResultPoint.objects.create(exercise_result=result, point_tracked=tracked, max_angle=80, min_angle=20)
        return exercise

    def requests(self):
        exercise = Exercise.objects.order_by('id').first()
        result = ExerciseResult.objects.order_by('id').first()
        st, tr = self.schedule.id, self.training.id
        return [
            (self.staff, '/routines/'),
            (self.staff, f'/routines/{self.routine.id}/'),
            (self.staff, '/exercises/'),
            (self.staff, f'/exercises/{exercise.id}/'),
            (self.staff, '/therapies/'),
            (self.staff, f'/therapies/{self.therapy.id}/'),
            (self.staff, f'/therapies/{self.therapy.id}/routines/'),
            (self.patient, '/scheduled_training'),
            (self.patient, f'/scheduled_training?scheduled_training_id={st}'),
            (self.staff, f'/scheduled_training?patient_id={self.tp.id}'),
            (self.staff, f'/scheduled_training?routine_id={self.routine.id}'),
            (self.patient, f'/scheduled_training/diffs?scheduled_training_id={st}&exercise_id={exercise.id}'),
            (self.staff, f'/therapist_patient?patient_id={self.tp.id}'),
            (self.staff, f'/training?training_id={tr}'),
            (self.staff, f'/training?scheduled_training_id={st}'),
            (self.staff, '/training'),
            (self.patient, f'/exercise_results?training_id={tr}'),
            (self.patient, f'/exercise_results?exercise_result_id={result.id}'),
            (self.staff, '/exercise_results'),
        ]

    def test_no_listing_selects_the_movement(self):
        for client, url in self.requests():
            with self.subTest(url=url), capture_sql() as sqls:
                response = client.get(url)
                self.assertEqual(response.status_code, 200, response.content[:200])
                self.assertEqual(selects_with_movement(sqls), [])

    def test_query_count_does_not_grow_with_exercises(self):
        before = {}
        for client, url in self.requests():
            with capture_sql() as sqls:
                client.get(url)
            before[url] = len(sqls)
        for _ in range(3):
            self.add_exercise()
        for client, url in self.requests():
            with self.subTest(url=url), capture_sql() as sqls:
                client.get(url)
                self.assertEqual(len(sqls), before[url])

    def test_movement_endpoints_still_return_it(self):
        exercise = Exercise.objects.order_by('id').first()
        result = ExerciseResult.objects.order_by('id').first()
        self.assertEqual(self.staff.get(f'/exercises/{exercise.id}/movement/').json(), MOVEMENT)
        self.assertEqual(self.staff.get(f'/exercise_results/{result.id}/movement').json(), MOVEMENT)

    def test_default_manager_and_related_managers_defer_the_movement(self):
        self.assertIn('movement', Exercise.objects.first().get_deferred_fields())
        self.assertIn('movement', self.routine.exercises.first().get_deferred_fields())
        self.assertIn('movement', ExerciseResult.objects.first().get_deferred_fields())
        self.assertIn('movement', self.training.results.first().get_deferred_fields())
        # Pedirlo sigue funcionando.
        self.assertEqual(Exercise.objects.first().movement, MOVEMENT)

    def test_editing_an_exercise_keeps_its_movement(self):
        # Con el campo aplazado, save() escribe solo lo cargado: un PATCH ya no
        # reescribe el movimiento (antes lo leía y lo volvía a guardar entero).
        exercise = Exercise.objects.order_by('id').first()
        response = self.staff.patch(f'/exercises/{exercise.id}/', {'name': 'Otro nombre'}, format='json')
        self.assertEqual(response.status_code, 200)
        exercise = Exercise.objects.defer(None).get(pk=exercise.pk)
        self.assertEqual(exercise.name, 'Otro nombre')
        self.assertEqual(exercise.movement, MOVEMENT)


class CallbackDoesNotLoadOldMovementTests(V2CallbackTestCase):
    """El callback reemplaza el movimiento sin leer el anterior ni el de la referencia."""

    def setUp(self):
        super().setUp()
        Exercise.objects.filter(pk=self.exercise.pk).update(movement=MOVEMENT)
        ExerciseResult.objects.filter(pk=self.result.pk).update(movement=MOVEMENT)

    def test_patient_callback(self):
        with capture_sql() as sqls:
            self.assertEqual(self.send(self.patient_url(), STANDARD).status_code, 200)
        self.assertEqual(selects_with_movement(sqls), [])
        self.assertEqual(len(ExerciseResult.objects.defer(None).get(pk=self.result.pk).movement['frames']), 2)
        # La referencia no se tocó.
        self.assertEqual(Exercise.objects.defer(None).get(pk=self.exercise.pk).movement, MOVEMENT)

    def test_reference_callback(self):
        with capture_sql() as sqls:
            self.assertEqual(self.send(self.reference_url(), STANDARD).status_code, 200)
        self.assertEqual(selects_with_movement(sqls), [])
        self.assertEqual(len(Exercise.objects.defer(None).get(pk=self.exercise.pk).movement['frames']), 2)
