"""
Dificultad de una asignación (/scheduled_training/diffs) y articulaciones
rastreadas al cambiar el video de referencia (actualización en sitio).

El servicio HTTP siempre se simula: dispatch_after_commit es un mock.
"""
import json
import time
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import Client, TestCase
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from telerehabilitation_API.processing.hmac_auth import compute_signature
from telerehabilitation_API.processing.results import video_digest
from telerehabilitation_API.therapy.models import (
    DifficultyRange, Exercise, ExerciseDifficulty, ExerciseResult, ExerciseSkeletonPointTracked,
    ScheduledTraining, ScheduledTrainingDifficulty,
)
from telerehabilitation_API.therapy.models.exercise_result_point import ExerciseResultPoint

SECRET = 'test-hmac-secret'  # PROCESSING_HMAC_SECRET de settings_test.py
DISPATCH_AFTER_COMMIT = 'telerehabilitation_API.processing.client.dispatch_after_commit'
OLD_VIDEO = 'https://supabase.test/storage/v1/object/public/videos/exercises/1111aaaa-0b5c-4c47-9f0e-2a413f9e5a6b.mp4'
NEW_VIDEO = 'https://supabase.test/storage/v1/object/public/videos/exercises/2222bbbb-0b5c-4c47-9f0e-2a413f9e5a6b.mp4'


def therapist_client():
    # Los grupos (Admin, Therapist, Patient) vienen del fixture de la plataforma.
    if not Group.objects.filter(name='Therapist').exists():
        call_command('loaddata', settings.BASE_DIR / 'initial_data.json', verbosity=0)
    user = User.objects.create_user(f'terapeuta-{User.objects.count()}', password='x-pass-123')
    Group.objects.get(name='Therapist').user_set.add(user)
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=user).key}')
    return api


class DiffsEndpointTests(TestCase):
    def setUp(self):
        self.api = therapist_client()
        self.exercise = Exercise.objects.create(name='Brazo', description='', order=1)
        self.elbow = ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=13)
        self.scheduled = ScheduledTraining.objects.create(start_time=timezone.now())

    def get(self):
        return self.api.get(
            f'/scheduled_training/diffs?scheduled_training_id={self.scheduled.id}&exercise_id={self.exercise.id}'
        )

    def test_without_difficulty_is_200_with_null(self):
        response = self.get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'difficulty': None})

    def test_with_difficulty_returns_its_ranges(self):
        difficulty = ExerciseDifficulty.objects.create(exercise=self.exercise, name='Fácil', description='')
        DifficultyRange.objects.create(difficulty=difficulty, point_tracked=self.elbow, min_angle=50, max_angle=140)
        ScheduledTrainingDifficulty.objects.create(
            difficulty=difficulty, exercise=self.exercise, scheduled_training=self.scheduled,
        )
        ranges = self.get().json()['difficulty']['ranges']
        self.assertEqual(len(ranges), 1)
        self.assertEqual(ranges[0]['point_tracked']['skeleton_point_id'], 13)
        self.assertEqual((ranges[0]['min_angle'], ranges[0]['max_angle']), (50, 140))

    def test_missing_parameters_is_400(self):
        self.assertEqual(self.api.get('/scheduled_training/diffs').status_code, 400)


@mock.patch(DISPATCH_AFTER_COMMIT)
class ReferenceVideoReuploadTests(TestCase):
    def setUp(self):
        self.api = therapist_client()
        self.exercise = Exercise.objects.create(
            name='Brazo', description='', order=1, status=Exercise.PROCESSED, video=OLD_VIDEO,
        )
        self.elbow = ExerciseSkeletonPointTracked.objects.create(
            exercise=self.exercise, skeleton_point_id=13, max_angle=150.0, min_angle=40.0,
        )
        self.knee = ExerciseSkeletonPointTracked.objects.create(
            exercise=self.exercise, skeleton_point_id=25, max_angle=170.0, min_angle=90.0,
        )
        difficulty = ExerciseDifficulty.objects.create(exercise=self.exercise, name='Fácil', description='')
        self.elbow_range = DifficultyRange.objects.create(
            difficulty=difficulty, point_tracked=self.elbow, min_angle=50, max_angle=140,
        )
        self.knee_range = DifficultyRange.objects.create(
            difficulty=difficulty, point_tracked=self.knee, min_angle=80, max_angle=160,
        )
        result = ExerciseResult.objects.create(exercise=self.exercise, status=ExerciseResult.PROCESSED)
        self.result_point = ExerciseResultPoint.objects.create(
            exercise_result=result, point_tracked=self.elbow, max_angle=130.0, min_angle=55.0,
        )

    def upload(self, points, video=NEW_VIDEO):
        return self.api.post(f'/exercises/{self.exercise.id}/video/', {'video': video, 'points': points},
                             format='json')

    def joints(self):
        return {t.skeleton_point_id: t for t in ExerciseSkeletonPointTracked.objects.filter(exercise=self.exercise)}

    def test_kept_joints_keep_their_id_and_ranges(self, dispatch):
        response = self.upload(json.dumps([
            {'skeleton_point_id': 13, 'left_point_id': 12, 'right_point_id': 15},
            {'skeleton_point_id': 11},
        ]))
        self.assertEqual(response.status_code, 200, response.content)
        joints = self.joints()
        self.assertEqual(set(joints), {13, 11})

        # La que sigue: misma fila, vecinos nuevos, sin la medición del video anterior.
        elbow = joints[13]
        self.assertEqual(elbow.id, self.elbow.id)
        self.assertEqual((elbow.left_point_id, elbow.right_point_id), (12, 15))
        self.assertEqual((elbow.max_angle, elbow.min_angle), (None, None))

        # Sus rangos y las mediciones de pacientes siguen enlazados.
        self.elbow_range.refresh_from_db()
        self.assertEqual(self.elbow_range.point_tracked_id, self.elbow.id)
        self.result_point.refresh_from_db()
        self.assertEqual(self.result_point.point_tracked_id, self.elbow.id)

        # La nueva se crea; solo la quitada se borra (su rango queda sin articulación).
        self.assertNotIn(joints[11].id, (self.elbow.id, self.knee.id))
        self.assertFalse(ExerciseSkeletonPointTracked.objects.filter(id=self.knee.id).exists())
        self.knee_range.refresh_from_db()
        self.assertIsNone(self.knee_range.point_tracked_id)

        self.exercise.refresh_from_db()
        self.assertEqual((self.exercise.status, self.exercise.video), (Exercise.PROCESSING, NEW_VIDEO))
        dispatch.assert_called_once()

    def test_neighbors_can_be_cleared(self, dispatch):
        ExerciseSkeletonPointTracked.objects.filter(id=self.elbow.id).update(left_point_id=12, right_point_id=15)
        self.upload(json.dumps([{'skeleton_point_id': 13}, {'skeleton_point_id': 25}]))
        elbow = self.joints()[13]
        self.assertEqual((elbow.id, elbow.left_point_id, elbow.right_point_id), (self.elbow.id, None, None))

    def test_legacy_comma_format(self, dispatch):
        self.assertEqual(self.upload('13,25,11').status_code, 200)
        joints = self.joints()
        self.assertEqual(set(joints), {13, 25, 11})
        self.assertEqual((joints[13].id, joints[25].id), (self.elbow.id, self.knee.id))

    def test_repeated_joint_in_request_is_created_once(self, dispatch):
        self.upload(json.dumps([{'skeleton_point_id': 13}, {'skeleton_point_id': 13, 'left_point_id': 12}]))
        joints = ExerciseSkeletonPointTracked.objects.filter(exercise=self.exercise, skeleton_point_id=13)
        self.assertEqual(joints.count(), 1)
        self.assertIsNone(joints.get().left_point_id)  # vale la primera

    def test_legacy_duplicate_rows_are_merged_keeping_ranges(self, dispatch):
        duplicate = ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=13)
        duplicate_range = DifficultyRange.objects.create(
            difficulty=self.elbow_range.difficulty, point_tracked=duplicate, min_angle=60, max_angle=120,
        )
        self.upload(json.dumps([{'skeleton_point_id': 13}]))
        self.assertEqual(list(self.joints()), [13])
        self.assertEqual(self.joints()[13].id, self.elbow.id)
        duplicate_range.refresh_from_db()
        self.assertEqual(duplicate_range.point_tracked_id, self.elbow.id)


class StaleCallbackAfterReuploadTests(TestCase):
    """La huella ?video= sigue rechazando avisos del video anterior aunque los ids se conserven."""

    def setUp(self):
        self.http = Client(enforce_csrf_checks=True)
        self.exercise = Exercise.objects.create(
            name='Brazo', description='', order=1, status=Exercise.PROCESSED, video=OLD_VIDEO,
        )
        self.elbow = ExerciseSkeletonPointTracked.objects.create(
            exercise=self.exercise, skeleton_point_id=13, max_angle=150.0, min_angle=40.0,
        )
        with mock.patch(DISPATCH_AFTER_COMMIT):
            response = therapist_client().post(
                f'/exercises/{self.exercise.id}/video/',
                {'video': NEW_VIDEO, 'points': json.dumps([{'skeleton_point_id': 13}])}, format='json',
            )
        self.assertEqual(response.status_code, 200)

    def send(self, digest, max_angle):
        payload = {
            'error': None, 'contract_version': 2, 'max_angle': None, 'min_angle': None,
            'results': {
                'points': [], 'fps': 30.0, 'duration_s': 0.1,
                'joints': [{'points': [11, 13, 15], 'vertex': 13, 'max_angle': max_angle,
                            'min_angle': 45.0, 'valid_frames': 1}],
                'frames': [{'t': 0.0, 'landmarks': [[0.5, 0.5, 0.0, 0.9]] * 33,
                            'world_landmarks': [[0.1, 0.2, 0.0]] * 33,
                            'angles_2d': [max_angle], 'angles_3d': [max_angle]}],
            },
        }
        raw = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        timestamp = str(int(time.time()))
        return self.http.post(
            f'/exercises/{self.exercise.id}/video_results/?video={digest}', data=raw,
            content_type='application/json', HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_SIGNATURE=compute_signature(timestamp, raw, SECRET),
        )

    def test_callback_of_the_previous_video_is_404_and_changes_nothing(self):
        self.assertEqual(self.send(video_digest(OLD_VIDEO), 150.0).status_code, 404)
        self.elbow.refresh_from_db()
        self.assertEqual((self.elbow.max_angle, self.elbow.min_angle), (None, None))
        self.exercise.refresh_from_db()
        self.assertEqual(self.exercise.status, Exercise.PROCESSING)
        self.assertIsNone(self.exercise.movement)

    def test_callback_of_the_new_video_measures_the_same_row(self):
        self.assertEqual(self.send(video_digest(NEW_VIDEO), 120.0).status_code, 200)
        self.elbow.refresh_from_db()
        self.assertEqual((self.elbow.max_angle, self.elbow.min_angle), (120.0, 45.0))
        self.exercise.refresh_from_db()
        self.assertEqual(self.exercise.status, Exercise.PROCESSED)
