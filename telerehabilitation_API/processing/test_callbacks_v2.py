"""
Callback v2 (docs/DECISIONES_TECNICAS.md, fase V2): un aviso por video con todas las
articulaciones (`joints`) y los 33 puntos por frame (`frames`).

Los callbacks v1 (con `point_tracked`) siguen en test_callbacks.py: se aceptan
durante la transición, para los jobs enviados por el backend v1.
"""
import json
import time
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import Client, TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from telerehabilitation_API.test_utils import therapist_user
from telerehabilitation_API.processing.callbacks import CALLBACK_MAX_BYTES
from telerehabilitation_API.processing.hmac_auth import compute_signature
from telerehabilitation_API.processing.results import (
    MISSING_NEIGHBORS_ERROR, NO_PERSON_ERROR, NOT_RETURNED_ERROR, video_digest,
)
from telerehabilitation_API.therapy.models import Exercise, ExerciseResult, ExerciseSkeletonPointTracked
from telerehabilitation_API.therapy.models.exercise_result_point import ExerciseResultPoint

SECRET = 'test-hmac-secret'  # PROCESSING_HMAC_SECRET de settings_test.py
PATIENT_VIDEO = 'https://supabase.test/storage/v1/object/public/videos/results/0b5c3f9e-2a41-4c47-9f0e-1c2d3e4f5a6b.mp4'
REFERENCE_VIDEO = 'https://supabase.test/storage/v1/object/public/videos/exercises/1c2d3e4f-0b5c-4c47-9f0e-2a413f9e5a6b.mp4'
ELBOW = [11, 13, 15]      # codo izquierdo: default del esqueleto
SHOULDER = [13, 11, 23]   # hombro izquierdo: default del esqueleto


def landmarks(seed=0.0):
    return [[round(0.01 * i + seed, 4), 0.5, -0.1, 0.99] for i in range(33)]


def world(seed=0.0):
    return [[round(0.02 * i + seed, 4), -0.3, 0.05] for i in range(33)]


def frame(t, angles_2d, angles_3d=None, pose=True):
    return {
        't': t,
        'landmarks': landmarks(t) if pose else None,
        'world_landmarks': world(t) if pose else None,
        'angles_2d': angles_2d if pose else [None] * len(angles_2d),
        'angles_3d': (angles_3d or angles_2d) if pose else [None] * len(angles_2d),
    }


def v2_payload(joints, frames, error=None, duration_s=0.1):
    return {
        'error': error,
        'contract_version': 2,
        'max_angle': None,
        'min_angle': None,
        'results': {'points': [], 'fps': 30.0, 'duration_s': duration_s, 'joints': joints, 'frames': frames},
    }


def joint(points, max_angle, min_angle, valid=2):
    return {'points': points, 'vertex': points[1], 'max_angle': max_angle,
            'min_angle': min_angle, 'valid_frames': valid}


def body(data):
    """Serialización exacta del servicio (callback_delivery.py)."""
    return json.dumps(data, separators=(',', ':')).encode('utf-8')


# Elbow en el índice 0 y hombro en el 1; frame 0.0333 sin pose.
STANDARD = v2_payload(
    [joint(ELBOW, 120.5, 45.25), joint(SHOULDER, 80.0, 30.0)],
    [frame(0.0, [45.25, 30.0], [47.0, 31.0]), frame(0.0333, [None, None], pose=False),
     frame(0.0667, [120.5, 80.0], [118.0, 82.0])],
)


class V2CallbackTestCase(TestCase):
    def setUp(self):
        # Sin token y con CSRF activo: así llega el servicio.
        self.http = Client(enforce_csrf_checks=True)
        self.exercise = Exercise.objects.create(
            name='Brazo', description='', order=1, status=Exercise.PROCESSING, video=REFERENCE_VIDEO,
        )
        self.elbow = ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=13)
        self.shoulder = ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=11)
        self.result = ExerciseResult.objects.create(
            exercise=self.exercise, status=ExerciseResult.PROCESSING, video=PATIENT_VIDEO,
        )

    def patient_url(self, digest=None):
        digest = video_digest(PATIENT_VIDEO) if digest is None else digest
        return f'/exercise_results/{self.result.id}/results' + (f'?video={digest}' if digest else '')

    def reference_url(self, digest=None):
        digest = video_digest(REFERENCE_VIDEO) if digest is None else digest
        return f'/exercises/{self.exercise.id}/video_results/' + (f'?video={digest}' if digest else '')

    def send(self, url, data=None, *, raw=None, signature=None):
        raw = raw if raw is not None else body(data)
        timestamp = str(int(time.time()))
        return self.http.post(
            url, data=raw, content_type='application/json',
            HTTP_X_TIMESTAMP=timestamp,
            HTTP_X_SIGNATURE=signature or compute_signature(timestamp, raw, SECRET),
        )

    def point(self, tracked):
        return ExerciseResultPoint.objects.get(exercise_result=self.result, point_tracked=tracked)

    def refresh(self):
        self.result.refresh_from_db()
        self.exercise.refresh_from_db()


class PatientV2CallbackTests(V2CallbackTestCase):
    def test_success_maps_each_joint_and_stores_the_movement(self):
        self.assertEqual(self.send(self.patient_url(), STANDARD).status_code, 200)
        self.refresh()

        elbow, shoulder = self.point(self.elbow), self.point(self.shoulder)
        self.assertEqual((elbow.max_angle, elbow.min_angle, elbow.error), (120.5, 45.25, None))
        self.assertEqual((shoulder.max_angle, shoulder.min_angle), (80.0, 30.0))
        self.assertEqual(elbow.angle_series, [
            {'t_ms': 0.0, 'angle': 45.25}, {'t_ms': 33.3, 'angle': None}, {'t_ms': 66.7, 'angle': 120.5},
        ])
        self.assertEqual(self.result.status, ExerciseResult.PROCESSED)

        movement = self.result.movement
        self.assertEqual(
            {k: movement[k] for k in ('exercise_id', 'exercise_name', 'duration', 'source', 'model')},
            {'exercise_id': self.exercise.id, 'exercise_name': 'Brazo', 'duration': 0.1,
             'source': 'service', 'model': 'mediapipe_heavy'},
        )
        # El frame sin pose no entra: el panel solo guarda frames con 33 puntos.
        self.assertEqual([f['t'] for f in movement['frames']], [0.0, 0.0667])
        first = movement['frames'][0]
        self.assertEqual(len(first['points']), 33)
        self.assertEqual(len(first['points'][0]), 4)
        self.assertEqual(len(first['world_points']), 33)
        self.assertEqual(first['angles'], {'LEFT_ELBOW': 45.25, 'LEFT_SHOULDER': 30.0})
        self.assertEqual(first['angles_3d'], {'LEFT_ELBOW': 47.0, 'LEFT_SHOULDER': 31.0})

    def test_mapping_is_by_full_triplet_not_by_position(self):
        swapped = v2_payload(
            [joint(SHOULDER, 80.0, 30.0), joint(ELBOW, 120.5, 45.25)],
            [frame(0.0, [30.0, 45.25])],
        )
        self.send(self.patient_url(), swapped)
        self.assertEqual(self.point(self.elbow).max_angle, 120.5)
        self.assertEqual(self.point(self.shoulder).max_angle, 80.0)
        self.refresh()
        self.assertEqual(self.result.movement['frames'][0]['angles'],
                         {'LEFT_ELBOW': 45.25, 'LEFT_SHOULDER': 30.0})

    def test_same_vertex_with_other_neighbors_is_not_confused(self):
        # Una terna con el mismo vértice pero otros vecinos NO es esta articulación.
        other = v2_payload([joint([12, 13, 15], 99.0, 9.0), joint(SHOULDER, 80.0, 30.0)],
                           [frame(0.0, [9.0, 30.0])])
        self.send(self.patient_url(), other)
        self.assertEqual(self.point(self.elbow).error, NOT_RETURNED_ERROR)
        self.refresh()
        self.assertEqual(self.result.status, ExerciseResult.ERROR)

    def test_duplicate_callback_is_idempotent(self):
        for _ in range(2):
            self.assertEqual(self.send(self.patient_url(), STANDARD).status_code, 200)
        self.assertEqual(ExerciseResultPoint.objects.filter(exercise_result=self.result).count(), 2)
        self.refresh()
        self.assertEqual(self.result.status, ExerciseResult.PROCESSED)
        self.assertEqual(len(self.result.movement['frames']), 2)

    def test_null_angles_are_a_joint_error(self):
        data = v2_payload([joint(ELBOW, None, None, 0), joint(SHOULDER, 80.0, 30.0)],
                          [frame(0.0, [None, 30.0])])
        self.send(self.patient_url(), data)
        self.assertEqual(self.point(self.elbow).error, NO_PERSON_ERROR)
        self.refresh()
        self.assertEqual(self.result.status, ExerciseResult.ERROR)
        self.assertEqual(self.result.movement['frames'][0]['angles'], {'LEFT_SHOULDER': 30.0})

    def test_job_error_puts_every_joint_in_error_and_keeps_no_movement(self):
        data = v2_payload([], [], error='processing_timeout')
        self.assertEqual(self.send(self.patient_url(), data).status_code, 200)
        self.assertEqual(self.point(self.elbow).error, 'processing_timeout')
        self.assertEqual(self.point(self.shoulder).error, 'processing_timeout')
        self.refresh()
        self.assertEqual(self.result.status, ExerciseResult.ERROR)
        self.assertIsNone(self.result.movement)

    def test_joint_without_neighbors_stays_in_error(self):
        nose = ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=0)
        self.send(self.patient_url(), STANDARD)
        self.assertEqual(self.point(nose).error, MISSING_NEIGHBORS_ERROR)
        self.assertIsNone(self.point(self.elbow).error)

    def test_two_joints_with_the_same_triplet_share_the_entry(self):
        twin = ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=13)
        self.send(self.patient_url(), STANDARD)
        self.assertEqual(self.point(twin).max_angle, 120.5)
        self.refresh()
        self.assertEqual(self.result.status, ExerciseResult.PROCESSED)

    def test_malformed_entries_never_cause_a_500(self):
        weird = v2_payload(
            [joint(ELBOW, 'alto', 3), {'points': 'no'}, 5, joint(SHOULDER, 80.0, 30.0)],
            [frame(0.0, [1.0, 'x', None, 30.0]), {'t': 'ayer'}, 'basura',
             {'t': 0.1, 'landmarks': [[1, 2]], 'world_landmarks': None, 'angles_2d': None, 'angles_3d': 7}],
        )
        self.assertEqual(self.send(self.patient_url(), weird).status_code, 200)
        self.assertEqual(self.point(self.elbow).error, NO_PERSON_ERROR)
        self.assertEqual(self.point(self.shoulder).max_angle, 80.0)

    def test_a_stale_video_digest_is_404_and_changes_nothing(self):
        response = self.send(self.patient_url(digest='0' * 16), STANDARD)
        self.assertEqual(response.status_code, 404)
        self.assertFalse(ExerciseResultPoint.objects.exists())

    def test_without_digest_is_accepted(self):
        self.assertEqual(self.send(self.patient_url(digest=''), STANDARD).status_code, 200)

    def test_invalid_signature_is_401_and_changes_nothing(self):
        self.assertEqual(self.send(self.patient_url(), STANDARD, signature='0' * 64).status_code, 401)
        self.assertFalse(ExerciseResultPoint.objects.exists())

    def test_unknown_result_is_404(self):
        url = f'/exercise_results/99999/results?video={video_digest(PATIENT_VIDEO)}'
        self.assertEqual(self.send(url, STANDARD).status_code, 404)


class ReferenceV2CallbackTests(V2CallbackTestCase):
    def test_success_updates_joints_status_and_movement(self):
        self.assertEqual(self.send(self.reference_url(), STANDARD).status_code, 200)
        self.refresh()
        self.elbow.refresh_from_db()
        self.assertEqual((self.elbow.max_angle, self.elbow.min_angle), (120.5, 45.25))
        self.assertEqual(self.exercise.status, Exercise.PROCESSED)
        self.assertEqual(self.exercise.movement['frames'][1]['angles'],
                         {'LEFT_ELBOW': 120.5, 'LEFT_SHOULDER': 80.0})

    def test_null_angles_put_the_exercise_in_error(self):
        data = v2_payload([joint(ELBOW, None, None, 0), joint(SHOULDER, 80.0, 30.0)], [frame(0.0, [None, 30.0])])
        self.send(self.reference_url(), data)
        self.refresh()
        self.assertEqual(self.exercise.status, Exercise.ERROR)

    def test_callback_of_a_replaced_reference_video_is_404(self):
        # El terapeuta subió otro video: el aviso del anterior trae otra huella.
        stale_digest = video_digest(REFERENCE_VIDEO)
        self.exercise.video = REFERENCE_VIDEO.replace('1c2d3e4f', 'ffffffff')
        self.exercise.save()
        response = self.send(self.reference_url(digest=stale_digest), STANDARD)
        self.assertEqual(response.status_code, 404)
        self.elbow.refresh_from_db()
        self.assertIsNone(self.elbow.max_angle)


class CallbackBodyLimitTests(V2CallbackTestCase):
    """25 MB solo en los dos callbacks; el resto de la API conserva 2,5 MB."""

    def big_payload(self, frames):
        return v2_payload([joint(ELBOW, 120.5, 45.25), joint(SHOULDER, 80.0, 30.0)],
                          [frame(i / 30, [45.25, 30.0]) for i in range(frames)])

    def test_callback_over_django_default_is_accepted(self):
        raw = body(self.big_payload(2000))
        self.assertGreater(len(raw), int(2.5 * 1024 * 1024))
        self.assertEqual(self.send(self.patient_url(), raw=raw).status_code, 200)
        self.refresh()
        self.assertEqual(len(self.result.movement["frames"]), 2000)

    def test_callback_over_25_mb_is_413(self):
        raw = b'{' + b' ' * CALLBACK_MAX_BYTES + b'}'
        self.assertEqual(self.send(self.patient_url(), raw=raw).status_code, 413)
        self.assertEqual(self.send(self.reference_url(), raw=raw).status_code, 413)

    def test_other_endpoints_keep_the_default_limit(self):
        user = therapist_user()
        api = APIClient()
        api.force_authenticate(user)
        big = json.dumps({'kind': 'result', 'content_type': 'video/mp4',
                          'filename': 'x' * (3 * 1024 * 1024)})
        response = api.post('/storage/upload_url', big, content_type='application/json')
        self.assertEqual(response.status_code, 400)


class MovementEndpointTests(TestCase):
    def setUp(self):
        call_command('loaddata', settings.BASE_DIR / 'initial_data.json', verbosity=0)
        self.exercise = Exercise.objects.create(name='Brazo', description='', order=1,
                                                movement={'frames': [{'t': 0.0}], 'source': 'service'})
        self.result = ExerciseResult.objects.create(exercise=self.exercise,
                                                    movement={'frames': [{'t': 1.0}], 'source': 'service'})

    def client_for(self, group):
        self.users = getattr(self, 'users', 0) + 1
        user = User.objects.create_user(f'u-{group}-{self.users}', password='x-pass-123')
        Group.objects.get(name=group).user_set.add(user)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=user).key}')
        return api

    def urls(self):
        return (f'/exercises/{self.exercise.id}/movement/', f'/exercise_results/{self.result.id}/movement')

    def test_permissions(self):
        for url in self.urls():
            with self.subTest(url=url):
                self.assertEqual(APIClient().get(url).status_code, 401)
                self.assertEqual(self.client_for('Patient').get(url).status_code, 403)
        for group in ('Therapist', 'Admin'):
            api = self.client_for(group)
            exercise_url, result_url = self.urls()
            self.assertEqual(api.get(exercise_url).json(), self.exercise.movement)
            self.assertEqual(api.get(result_url).json(), self.result.movement)

    def test_404_without_movement(self):
        Exercise.objects.filter(pk=self.exercise.pk).update(movement=None)
        ExerciseResult.objects.filter(pk=self.result.pk).update(movement=None)
        api = self.client_for('Therapist')
        for url in self.urls():
            self.assertEqual(api.get(url).status_code, 404)

    def test_listings_do_not_include_the_movement(self):
        api = self.client_for('Therapist')
        exercise = api.get(f'/exercises/{self.exercise.id}/').json()
        self.assertNotIn('movement', exercise)
        result = api.get(f'/exercise_results?exercise_result_id={self.result.id}').json()
        self.assertNotIn('movement', result)
        self.assertNotIn('movement', json.dumps(api.get('/exercise_results').json()))

    @mock.patch('telerehabilitation_API.processing.client.dispatch_after_commit')
    def test_a_new_reference_video_clears_the_old_movement(self, dispatch):
        api = self.client_for('Therapist')
        response = api.post(f'/exercises/{self.exercise.id}/video/', {
            'video': REFERENCE_VIDEO, 'points': json.dumps([{'skeleton_point_id': 13}]),
        }, format='json')
        self.assertEqual(response.status_code, 200)
        self.exercise.refresh_from_db()
        self.assertIsNone(self.exercise.movement)
