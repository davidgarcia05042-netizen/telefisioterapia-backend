"""
Envíos huérfanos (processing/stale.py): lo que lleva más de 15 min
"procesando" sin callback pasa a error al leerlo, y un callback que llegue
tarde lo deja procesado.
"""
from datetime import timedelta

from django.utils import timezone
from rest_framework.test import APIClient

from telerehabilitation_API.processing.stale import STALE_AFTER, STALE_ERROR, expire_stale_processing
from telerehabilitation_API.processing.test_callbacks_v2 import STANDARD, V2CallbackTestCase
from telerehabilitation_API.test_utils import therapist_user
from telerehabilitation_API.therapy.models import Exercise, ExerciseResult
from telerehabilitation_API.therapy.models.exercise_result_point import ExerciseResultPoint


class StaleTestCase(V2CallbackTestCase):
    def setUp(self):
        super().setUp()
        self.api = APIClient()
        self.api.force_authenticate(therapist_user())

    def sent_ago(self, minutes):
        when = timezone.now() - timedelta(minutes=minutes)
        ExerciseResult.objects.filter(pk=self.result.pk).update(start_time=when)
        Exercise.objects.filter(pk=self.exercise.pk).update(processing_started_at=when)


class StalePatientResultTests(StaleTestCase):
    def test_reading_after_15_minutes_puts_it_in_error_with_the_reason(self):
        self.sent_ago(16)
        response = self.api.get(f'/exercise_results?exercise_result_id={self.result.id}')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'Error al procesar video')
        self.refresh()
        self.assertEqual(self.result.status, ExerciseResult.ERROR)
        self.assertEqual(self.point(self.elbow).error, STALE_ERROR)
        self.assertEqual(self.point(self.shoulder).error, STALE_ERROR)

    def test_the_list_by_session_also_checks(self):
        # La consulta de la app: GET /exercise_results?training_id=.
        self.sent_ago(16)
        self.api.get('/exercise_results?training_id=999')
        self.refresh()
        self.assertEqual(self.result.status, ExerciseResult.ERROR)

    def test_before_15_minutes_it_keeps_processing(self):
        self.sent_ago(14)
        self.api.get(f'/exercise_results?exercise_result_id={self.result.id}')
        self.refresh()
        self.assertEqual(self.result.status, ExerciseResult.PROCESSING)
        self.assertFalse(ExerciseResultPoint.objects.exists())

    def test_a_measured_joint_is_kept(self):
        ExerciseResultPoint.objects.create(
            exercise_result=self.result, point_tracked=self.elbow, max_angle=120.0, min_angle=40.0,
        )
        self.sent_ago(16)
        expire_stale_processing()
        self.refresh()
        self.assertEqual(self.result.status, ExerciseResult.ERROR)
        elbow = self.point(self.elbow)
        self.assertEqual((elbow.max_angle, elbow.min_angle, elbow.error), (120.0, 40.0, None))
        self.assertEqual(self.point(self.shoulder).error, STALE_ERROR)

    def test_a_closed_result_is_not_touched(self):
        # UPDATE condicional: solo cambia lo que sigue procesando.
        ExerciseResult.objects.filter(pk=self.result.pk).update(status=ExerciseResult.PROCESSED)
        self.sent_ago(60)
        expired_results, _references = expire_stale_processing()
        self.assertEqual(expired_results, 0)
        self.refresh()
        self.assertEqual(self.result.status, ExerciseResult.PROCESSED)

    def test_without_send_time_it_is_left_alone(self):
        ExerciseResult.objects.filter(pk=self.result.pk).update(start_time=None)
        expire_stale_processing(now=timezone.now() + timedelta(days=1))
        self.refresh()
        self.assertEqual(self.result.status, ExerciseResult.PROCESSING)

    def test_a_late_callback_is_accepted_and_leaves_it_processed(self):
        self.sent_ago(20)
        expire_stale_processing()
        self.refresh()
        self.assertEqual(self.result.status, ExerciseResult.ERROR)

        self.assertEqual(self.send(self.patient_url(), STANDARD).status_code, 200)
        self.refresh()
        self.assertEqual(self.result.status, ExerciseResult.PROCESSED)
        elbow, shoulder = self.point(self.elbow), self.point(self.shoulder)
        self.assertEqual((elbow.max_angle, elbow.min_angle, elbow.error), (120.5, 45.25, None))
        self.assertEqual((shoulder.max_angle, shoulder.min_angle, shoulder.error), (80.0, 30.0, None))
        self.assertIsNotNone(self.result.movement)

        # Y una lectura posterior no lo vuelve a pasar a error.
        self.api.get(f'/exercise_results?exercise_result_id={self.result.id}')
        self.refresh()
        self.assertEqual(self.result.status, ExerciseResult.PROCESSED)


class StaleReferenceTests(StaleTestCase):
    def test_reading_the_exercise_after_15_minutes_puts_it_in_error(self):
        self.sent_ago(16)
        response = self.api.get(f'/exercises/{self.exercise.id}/')
        self.assertEqual(response.status_code, 200)
        self.refresh()
        self.assertEqual(self.exercise.status, Exercise.ERROR)

    def test_the_exercise_list_also_checks(self):
        self.sent_ago(16)
        self.api.get('/exercises/')
        self.refresh()
        self.assertEqual(self.exercise.status, Exercise.ERROR)

    def test_before_15_minutes_it_keeps_processing(self):
        self.sent_ago(14)
        self.api.get(f'/exercises/{self.exercise.id}/')
        self.refresh()
        self.assertEqual(self.exercise.status, Exercise.PROCESSING)

    def test_a_late_callback_is_accepted_and_leaves_it_processed(self):
        self.sent_ago(20)
        self.api.get(f'/exercises/{self.exercise.id}/')
        self.refresh()
        self.assertEqual(self.exercise.status, Exercise.ERROR)

        self.assertEqual(self.send(self.reference_url(), STANDARD).status_code, 200)
        self.refresh()
        self.assertEqual(self.exercise.status, Exercise.PROCESSED)
        self.elbow.refresh_from_db()
        self.assertEqual((self.elbow.max_angle, self.elbow.min_angle), (120.5, 45.25))

    def test_the_threshold_is_15_minutes(self):
        self.assertEqual(STALE_AFTER, timedelta(minutes=15))
