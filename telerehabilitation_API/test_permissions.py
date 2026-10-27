"""
Matriz de permisos (M1, docs/DECISIONES_TECNICAS.md): cada endpoint × rol.

Cada endpoint se llama sin token y con token de paciente dueño de los datos,
de otro paciente, de terapeuta y de administrador, y se compara el código con
el esperado. 401 = sin sesión; 403 = el rol no puede; 404 = el recurso no es
suyo (no se revela que existe). Un 2xx o un 400 significa que el rol pasó el
control de permisos. Cada solicitud se revierte para que todos los roles vean
el mismo estado.

Además, cada ruta registrada en urls.py debe estar en la matriz: un endpoint
nuevo no puede quedar sin clasificar.

Supabase y el servicio de procesamiento se simulan.
"""
from unittest import mock

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.db import transaction
from django.test import TestCase
from django.urls import URLPattern, URLResolver, get_resolver, resolve
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from telerehabilitation_API.authentication.models import Admin, Patient, Therapist
from telerehabilitation_API.therapy.models import (
    DifficultyRange, Exercise, ExerciseDifficulty, ExerciseResult, ExerciseSkeletonPointTracked, Routine,
    ScheduledTraining, ScheduledTrainingDifficulty, Therapy, TherapyPatient, Training,
)

ROLES = ('anon', 'owner', 'other', 'therapist', 'admin')
PASSWORD = 'clave-matriz-123'
BUCKET = 'https://supabase.test/storage/v1/object/public/videos/'
RESULT_VIDEO = BUCKET + 'results/0b5c3f9e-2a41-4c47-9f0e-1c2d3e4f5a6b.mp4'
EXERCISE_VIDEO = BUCKET + 'exercises/1111aaaa-0b5c-4c47-9f0e-2a413f9e5a6b.mp4'
DISPATCH_AFTER_COMMIT = 'telerehabilitation_API.processing.client.dispatch_after_commit'
SIGNED_UPLOAD = 'telerehabilitation_API.storage.supabase.create_signed_upload_url'

# Rutas fuera de la API de DRF: sitio de administración de Django y documentación.
UNCLASSIFIED_PREFIXES = ('admin/', '^admin/', 'swagger', 'redoc')


class PermissionMatrixTests(TestCase):
    def setUp(self):
        call_command('loaddata', settings.BASE_DIR / 'initial_data.json', verbosity=0)

        def user(name, group, model):
            u = User.objects.create_user(name, f'{name}@test.local', PASSWORD)
            Group.objects.get(name=group).user_set.add(u)
            return u, model.objects.create(user=u)

        self.u_admin, _ = user('admin1', 'Admin', Admin)
        self.u_ther, therapist = user('tera1', 'Therapist', Therapist)
        self.u_owner, owner = user('pac1', 'Patient', Patient)
        self.u_other, other = user('pac2', 'Patient', Patient)

        self.therapy = Therapy.objects.create(name='Rodilla', description='')
        self.routine = Routine.objects.create(therapy=self.therapy, name='Rutina', description='')
        self.exercise = Exercise.objects.create(routine=self.routine, name='Codo', description='', order=1,
                                                video=EXERCISE_VIDEO, status=Exercise.PROCESSED,
                                                movement={'frames': []})
        self.outside = Exercise.objects.create(name='Fuera de la rutina', description='', order=1)
        self.tracked = ExerciseSkeletonPointTracked.objects.create(exercise=self.exercise, skeleton_point_id=13)
        self.difficulty = ExerciseDifficulty.objects.create(exercise=self.exercise, name='Fácil', description='')
        DifficultyRange.objects.create(difficulty=self.difficulty, point_tracked=self.tracked, min_angle=1, max_angle=2)
        self.free_difficulty = ExerciseDifficulty.objects.create(exercise=self.exercise, name='Libre', description='')
        self.tp = TherapyPatient.objects.create(therapy=self.therapy, therapist=therapist, patient=owner)
        self.other_tp = TherapyPatient.objects.create(therapy=self.therapy, therapist=therapist, patient=other)
        self.st = ScheduledTraining.objects.create(routine=self.routine, therapy_patient=self.tp,
                                                   start_time=timezone.now())
        ScheduledTrainingDifficulty.objects.create(difficulty=self.difficulty, exercise=self.exercise,
                                                   scheduled_training=self.st)
        self.training = Training.objects.create(schedule_training=self.st, start_time=timezone.now())
        self.result = ExerciseResult.objects.create(exercise=self.exercise, training=self.training,
                                                    video=RESULT_VIDEO, status=ExerciseResult.PROCESSED,
                                                    movement={'frames': []})

        self.clients = {'anon': APIClient(raise_request_exception=False)}
        for role, u in (('owner', self.u_owner), ('other', self.u_other),
                        ('therapist', self.u_ther), ('admin', self.u_admin)):
            self.clients[role] = self.client_for(u)

    def client_for(self, user):
        client = APIClient(raise_request_exception=False)
        client.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=user).key}')
        return client

    def matrix(self):
        e, d, st, tr, r = self.exercise.id, self.difficulty.id, self.st.id, self.training.id, self.result.id
        therapy, routine = self.therapy.id, self.routine.id
        new_user = {'username': 'nuevo', 'email': 'n@test.local', 'first_name': 'N', 'last_name': 'U'}
        change = {'password': PASSWORD, 'new_password': 'Otra-clave-segura-456'}
        # (método, ruta, cuerpo, códigos esperados para sin token, dueño, otro paciente, terapeuta, admin)
        return [
            ('POST', '/auth/users/login/', {'username': 'pac1', 'password': PASSWORD}, (201, 201, 201, 201, 201)),
            ('POST', '/auth/users/logout/', None, (401, 204, 204, 204, 204)),
            # Cada usuario cambia la suya (aquí todos tienen la misma contraseña).
            ('POST', '/auth/users/change_password/', change, (401, 201, 201, 201, 201)),
            ('GET', '/auth/users/', None, (401, 200, 200, 200, 200)),
            ('GET', f'/auth/users/{self.u_owner.id}/', None, (401, 200, 404, 200, 200)),
            ('GET', '/patients', None, (401, 403, 403, 200, 200)),
            ('POST', '/patients', new_user, (401, 403, 403, 403, 201)),
            ('GET', '/therapists', None, (401, 403, 403, 200, 200)),
            ('POST', '/therapists', new_user, (401, 403, 403, 403, 201)),
            ('GET', '/admins', None, (401, 403, 403, 403, 200)),
            ('POST', '/admins', new_user, (401, 403, 403, 403, 201)),
            ('GET', '/therapies/', None, (401, 403, 403, 200, 200)),
            ('POST', '/therapies/', {'name': 'X', 'description': 'Y'}, (401, 403, 403, 201, 201)),
            ('GET', f'/therapies/{therapy}/', None, (401, 403, 403, 200, 200)),
            ('PATCH', f'/therapies/{therapy}/', {'name': 'Z'}, (401, 403, 403, 200, 200)),
            ('DELETE', f'/therapies/{therapy}/', None, (401, 403, 403, 204, 204)),
            ('GET', f'/therapies/{therapy}/get_patients/', None, (401, 403, 403, 200, 200)),
            ('POST', f'/therapies/{therapy}/enroll_patient/',
             {'therapist': 1, 'therapy': therapy, 'patient': 1}, (401, 403, 403, 200, 200)),
            ('GET', f'/therapies/{therapy}/routines/', None, (401, 403, 403, 200, 200)),
            ('GET', '/routines/', None, (401, 403, 403, 200, 200)),
            ('POST', '/routines/', {'name': 'X', 'description': 'Y', 'therapy_id': therapy}, (401, 403, 403, 201, 201)),
            ('GET', f'/routines/{routine}/', None, (401, 403, 403, 200, 200)),
            ('PATCH', f'/routines/{routine}/', {'name': 'Z'}, (401, 403, 403, 200, 200)),
            ('DELETE', f'/routines/{routine}/', None, (401, 403, 403, 204, 204)),
            ('GET', '/exercises/', None, (401, 403, 403, 200, 200)),
            ('POST', '/exercises/', {'name': 'X', 'description': 'Y', 'order': 1}, (401, 403, 403, 201, 201)),
            ('GET', f'/exercises/{e}/', None, (401, 403, 403, 200, 200)),
            ('PATCH', f'/exercises/{e}/', {'name': 'Z'}, (401, 403, 403, 200, 200)),
            ('DELETE', f'/exercises/{e}/', None, (401, 403, 403, 204, 204)),
            ('POST', f'/exercises/{e}/video/', {'video': EXERCISE_VIDEO, 'points': '[]'}, (401, 403, 403, 200, 200)),
            ('GET', f'/exercises/{e}/movement/', None, (401, 403, 403, 200, 200)),
            ('GET', f'/exercises/{e}/points_tracked/', None, (401, 403, 403, 200, 200)),
            ('GET', f'/exercises/{e}/difficulties/', None, (401, 403, 403, 200, 200)),
            ('POST', f'/exercises/{e}/post_difficulty/', {'name': 'X', 'description': 'Y', 'ranges': '[]'},
             (401, 403, 403, 201, 201)),
            ('GET', f'/exercises/{e}/difficulties/{d}/', None, (401, 403, 403, 200, 200)),
            ('PUT', f'/exercises/{e}/difficulties/{d}/',
             {'name': 'Z', 'description': '', 'ranges': [{'point_tracked_id': self.tracked.id, 'min_angle': 1, 'max_angle': 2}]},
             (401, 403, 403, 200, 200)),
            ('DELETE', f'/exercises/{e}/difficulties/{self.free_difficulty.id}/', None, (401, 403, 403, 204, 204)),
            ('GET', '/skeleton/', None, (401, 403, 403, 200, 200)),
            ('GET', '/skeleton/13/', None, (401, 403, 403, 200, 200)),
            ('PATCH', '/skeleton/13/', {'verbose': 'Codo'}, (401, 403, 403, 200, 200)),
            ('PATCH', '/skeleton/bulk_update/', [], (401, 403, 403, 200, 200)),
            # Sin parámetros: las del usuario (el panel no tiene: 404, como siempre).
            ('GET', '/scheduled_training', None, (401, 200, 404, 404, 404)),
            ('GET', f'/scheduled_training?scheduled_training_id={st}', None, (401, 200, 404, 200, 200)),
            ('GET', f'/scheduled_training?patient_id={self.tp.id}', None, (401, 403, 403, 200, 200)),
            ('GET', f'/scheduled_training?routine_id={routine}', None, (401, 403, 403, 200, 200)),
            ('POST', '/scheduled_training', {'routine': routine, 'therapy_patient': self.tp.id,
                                             'start_time': '20/10/2026 09:30:00', 'difficulties': []},
             (401, 403, 403, 201, 201)),
            ('GET', f'/scheduled_training/diffs?scheduled_training_id={st}&exercise_id={e}', None,
             (401, 200, 404, 200, 200)),
            ('GET', f'/therapist_patient?patient_id={self.tp.id}', None, (401, 403, 403, 200, 200)),
            ('GET', '/training', None, (401, 403, 403, 200, 200)),
            ('POST', '/training', {'schedule_training_id': st}, (401, 201, 404, 201, 201)),
            ('POST', f'/training/end?training_id={tr}', None, (401, 200, 404, 200, 200)),
            ('POST', '/storage/upload_url', {'kind': 'result', 'content_type': 'video/mp4'}, (401, 200, 200, 200, 200)),
            ('GET', '/exercise_results', None, (401, 403, 403, 200, 200)),
            ('GET', f'/exercise_results?training_id={tr}', None, (401, 200, 404, 200, 200)),
            ('GET', f'/exercise_results?exercise_result_id={r}', None, (401, 200, 404, 200, 200)),
            ('POST', '/exercise_results', {'training_id': tr, 'exercise_id': e, 'video': RESULT_VIDEO},
             (401, 201, 404, 201, 201)),
            ('POST', f'/exercise_results/{r}/concept', {'concept': 'Bien'}, (401, 403, 403, 201, 201)),
            ('GET', f'/exercise_results/{r}/movement', None, (401, 403, 403, 200, 200)),
            # El servicio se autentica con la firma HMAC, no con token: sin firma, 401 para todos.
            ('POST', f'/exercises/{e}/video_results/', {}, (401, 401, 401, 401, 401)),
            ('POST', f'/exercise_results/{r}/results', {}, (401, 401, 401, 401, 401)),
        ]

    def call(self, client, method, url, body):
        with transaction.atomic():
            send = getattr(client, method.lower())
            response = send(url) if body is None else send(url, body, format='json')
            transaction.set_rollback(True)
        return response.status_code

    @mock.patch(DISPATCH_AFTER_COMMIT)
    @mock.patch(SIGNED_UPLOAD, return_value='https://supabase.test/storage/v1/object/upload/sign/videos/x?token=t')
    def test_every_endpoint_and_role(self, *_):
        mismatches = []
        for method, url, body, expected in self.matrix():
            got = tuple(self.call(self.clients[role], method, url, body) for role in ROLES)
            if got != expected:
                mismatches.append(f'{method} {url}: esperado {expected}, obtenido {got}')
        self.assertEqual(mismatches, [], '\n' + '\n'.join(mismatches))

    def test_every_route_is_in_the_matrix(self):
        def flatten(patterns, prefix=''):
            for p in patterns:
                if isinstance(p, URLResolver):
                    yield from flatten(p.url_patterns, prefix + str(p.pattern))
                elif isinstance(p, URLPattern):
                    yield prefix + str(p.pattern)

        covered = {resolve(url.split('?')[0]).route for _, url, _, _ in self.matrix()}
        covered |= {'health'}
        missing = []
        for route in flatten(get_resolver().url_patterns):
            if route.startswith(UNCLASSIFIED_PREFIXES) or 'format' in route or route in ('', '^$'):
                continue
            if route not in covered:
                missing.append(route)
        self.assertEqual(missing, [], f'Rutas sin clasificar en la matriz: {missing}')

    # --- Lo suyo del paciente ---

    @mock.patch(DISPATCH_AFTER_COMMIT)
    def test_patient_uploads_a_result_for_an_exercise_of_their_routine(self, dispatch):
        owner = self.clients['owner']
        response = owner.post('/exercise_results', {'training_id': self.training.id, 'exercise_id': self.exercise.id,
                                                    'video': RESULT_VIDEO}, format='json')
        self.assertEqual(response.status_code, 201)
        dispatch.assert_called_once()
        outside = owner.post('/exercise_results', {'training_id': self.training.id, 'exercise_id': self.outside.id,
                                                   'video': RESULT_VIDEO}, format='json')
        self.assertEqual(outside.status_code, 400)
        self.assertIn('no pertenece a la rutina', outside.content.decode())

    @mock.patch(SIGNED_UPLOAD, return_value='https://supabase.test/storage/v1/object/upload/sign/videos/x?token=t')
    def test_patient_cannot_ask_an_upload_url_for_reference_videos(self, _):
        response = self.clients['owner'].post('/storage/upload_url',
                                              {'kind': 'exercise', 'content_type': 'video/mp4'}, format='json')
        self.assertEqual(response.status_code, 403)
        staff = self.clients['therapist'].post('/storage/upload_url',
                                               {'kind': 'exercise', 'content_type': 'video/mp4'}, format='json')
        self.assertEqual(staff.status_code, 200)

    def test_patient_lists_only_their_scheduled_trainings(self):
        ScheduledTraining.objects.create(routine=self.routine, therapy_patient=self.other_tp, start_time=timezone.now())
        ids = [item['id'] for item in self.clients['owner'].get('/scheduled_training').json()]
        self.assertEqual(ids, [self.st.id])

    def test_patient_sees_only_their_own_user(self):
        users = self.clients['owner'].get('/auth/users/').json()
        rows = users['results'] if isinstance(users, dict) else users
        self.assertEqual([u['username'] for u in rows], ['pac1'])

    def test_training_for_a_missing_scheduled_training_creates_nothing(self):
        response = self.clients['therapist'].post('/training', {'schedule_training_id': 9999}, format='json')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(Training.objects.count(), 1)

    def test_a_user_without_role_gets_403(self):
        nobody = self.client_for(User.objects.create_user('sin-rol', password=PASSWORD))
        for url in ('/therapies/', '/scheduled_training', '/exercise_results?training_id=1', '/training'):
            self.assertEqual(nobody.get(url).status_code, 403, url)
