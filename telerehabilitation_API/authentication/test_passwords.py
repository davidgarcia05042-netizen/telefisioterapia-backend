"""
Contraseñas (B1/DM4, docs/DECISIONES_TECNICAS.md): temporal aleatoria al crear cuentas, cambio
de contraseña con la actual y los validadores de Django, y el comando que
reemplaza la '123456789' de las cuentas viejas.
"""
import io
import re

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from telerehabilitation_API.authentication.accounts import (
    LEGACY_DEFAULT_PASSWORD, TEMPORARY_PASSWORD_LENGTH, generate_temporary_password,
)
from telerehabilitation_API.authentication.models import Admin, Patient, Therapist
from telerehabilitation_API.test_utils import user_in_group

NEW_USER = {'username': 'nuevo', 'email': 'nuevo@test.local', 'first_name': 'Ana', 'last_name': 'Pérez'}


def client_for(user):
    api = APIClient()
    api.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=user).key}')
    return api


class TemporaryPasswordTests(TestCase):
    def setUp(self):
        call_command('loaddata', settings.BASE_DIR / 'initial_data.json', verbosity=0)
        self.admin = client_for(user_in_group('Admin', 'admin1'))

    def test_generated_passwords_are_random_and_have_letters_and_digits(self):
        passwords = {generate_temporary_password() for _ in range(50)}
        self.assertEqual(len(passwords), 50)
        for password in passwords:
            self.assertEqual(len(password), TEMPORARY_PASSWORD_LENGTH)
            self.assertRegex(password, r'[0-9]')
            self.assertRegex(password, r'[A-Za-z]')
            self.assertNotRegex(password, r'[0O1lI]')

    def test_each_kind_of_account_gets_a_temporary_password_once(self):
        for path, model, group in (('/patients', Patient, 'Patient'), ('/therapists', Therapist, 'Therapist'),
                                   ('/admins', Admin, 'Admin')):
            body = {**NEW_USER, 'username': f'nuevo-{group}'}
            response = self.admin.post(path, body, format='json')
            self.assertEqual(response.status_code, 201, path)
            self.assertEqual(response['Cache-Control'], 'no-store')
            data = response.json()
            user = User.objects.get(username=f'nuevo-{group}')
            self.assertEqual((data['id'], data['username']), (user.id, user.username))
            self.assertTrue(user.check_password(data['temporary_password']), path)
            self.assertFalse(user.check_password(LEGACY_DEFAULT_PASSWORD), path)
            self.assertTrue(user.groups.filter(name=group).exists())
            self.assertTrue(model.objects.filter(user=user).exists())
            self.assertEqual((user.first_name, user.last_name), ('Ana', 'Pérez'))
            # Con la temporal se puede iniciar sesión.
            login = APIClient().post('/auth/users/login/', {'username': user.username,
                                                            'password': data['temporary_password']}, format='json')
            self.assertEqual(login.status_code, 201)

    def test_the_patient_keeps_their_profile_fields(self):
        response = self.admin.post('/patients', {**NEW_USER, 'height': 1.7, 'gender': 'F'}, format='json')
        self.assertEqual(response.status_code, 201)
        patient = Patient.objects.get(user__username='nuevo')
        self.assertEqual((patient.height, patient.gender), (1.7, 'F'))

    def test_missing_fields_or_duplicate_username_are_400_not_500(self):
        for field in ('username', 'email', 'first_name', 'last_name'):
            body = {k: v for k, v in NEW_USER.items() if k != field}
            response = self.admin.post('/patients', body, format='json')
            self.assertEqual(response.status_code, 400, field)
            self.assertIn('Falta', response.json()['detail'])
        self.assertEqual(self.admin.post('/patients', NEW_USER, format='json').status_code, 201)
        duplicate = self.admin.post('/therapists', {**NEW_USER, 'username': 'NUEVO'}, format='json')
        self.assertEqual(duplicate.status_code, 400)
        self.assertIn('Ya existe', duplicate.json()['detail'])
        self.assertEqual(User.objects.filter(username__iexact='nuevo').count(), 1)

    def test_no_default_password_left_in_the_code(self):
        root = settings.BASE_DIR / 'telerehabilitation_API'
        offenders = [str(path.relative_to(root)) for path in root.rglob('*.py')
                     if 'test' not in path.name and path.name != 'accounts.py'
                     and LEGACY_DEFAULT_PASSWORD in path.read_text(encoding='utf-8')]
        self.assertEqual(offenders, [])


class ChangePasswordTests(TestCase):
    def setUp(self):
        self.user = user_in_group('Patient', 'paciente', password='clave-actual-123')
        self.api = client_for(self.user)

    def change(self, **body):
        return self.api.post('/auth/users/change_password/', body, format='json')

    def test_requires_a_session(self):
        response = APIClient().post('/auth/users/change_password/',
                                    {'username': 'paciente', 'password': 'clave-actual-123',
                                     'new_password': 'Nueva-clave-segura-1'}, format='json')
        self.assertEqual(response.status_code, 401)

    def test_changes_the_password_with_the_current_one(self):
        self.assertEqual(self.change(password='clave-actual-123', new_password='Nueva-clave-segura-1').status_code, 201)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('Nueva-clave-segura-1'))

    def test_the_panel_body_with_username_still_works(self):
        response = self.change(username='paciente', password='clave-actual-123', new_password='Nueva-clave-segura-1')
        self.assertEqual(response.status_code, 201)

    def test_wrong_current_password_is_400(self):
        response = self.change(password='otra-cosa-123', new_password='Nueva-clave-segura-1')
        self.assertEqual(response.status_code, 400)
        self.assertIn('actual no es correcta', response.json()['detail'])

    def test_the_new_password_goes_through_django_validators_in_spanish(self):
        for weak in ('123', '12345678912', 'password123'):
            response = self.change(password='clave-actual-123', new_password=weak)
            self.assertEqual(response.status_code, 400, weak)
            self.assertRegex(response.json()['detail'], r'contraseña', weak)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('clave-actual-123'))

    def test_cannot_change_another_users_password(self):
        user_in_group('Patient', 'otro', password='clave-otro-123')
        response = self.change(username='otro', password='clave-otro-123', new_password='Nueva-clave-segura-1')
        self.assertEqual(response.status_code, 403)
        self.assertTrue(User.objects.get(username='otro').check_password('clave-otro-123'))


class ResetDefaultPasswordsCommandTests(TestCase):
    def setUp(self):
        self.legacy = [user_in_group('Patient', name, password=LEGACY_DEFAULT_PASSWORD) for name in ('viejo1', 'viejo2')]
        self.safe = user_in_group('Therapist', 'seguro', password='clave-propia-123')
        Token.objects.create(user=self.legacy[0])

    def run_command(self, *args):
        out = io.StringIO()
        call_command('reset_default_passwords', *args, stdout=out)
        return out.getvalue()

    def test_dry_run_lists_and_changes_nothing(self):
        output = self.run_command('--dry-run')
        self.assertIn('2 cuenta(s)', output)
        self.assertIn('viejo1', output)
        self.assertNotIn('seguro', output)
        self.assertTrue(User.objects.get(username='viejo1').check_password(LEGACY_DEFAULT_PASSWORD))

    def test_assigns_temporary_passwords_and_revokes_sessions(self):
        output = self.run_command()
        printed = dict(re.findall(r'^\s+(viejo\d)\s+(\S+)$', output, flags=re.M))
        self.assertEqual(set(printed), {'viejo1', 'viejo2'})
        for username, password in printed.items():
            user = User.objects.get(username=username)
            self.assertTrue(user.check_password(password))
            self.assertFalse(user.check_password(LEGACY_DEFAULT_PASSWORD))
        self.assertFalse(Token.objects.filter(user=self.legacy[0]).exists())
        self.assertTrue(User.objects.get(username='seguro').check_password('clave-propia-123'))
        self.assertIn('Ninguna cuenta', self.run_command())

    def test_user_option_issues_a_new_temporary_password_for_that_user(self):
        output = self.run_command('--user', 'seguro')
        printed = dict(re.findall(r'^\s+(\S+)\s+(\S+)$', output, flags=re.M))
        self.assertEqual(list(printed), ['seguro'])
        self.assertTrue(User.objects.get(username='seguro').check_password(printed['seguro']))
        # Las demás cuentas no se tocan.
        self.assertTrue(User.objects.get(username='viejo1').check_password(LEGACY_DEFAULT_PASSWORD))

    def test_user_option_with_an_unknown_user_fails_without_changes(self):
        with self.assertRaises(CommandError):
            self.run_command('--user', 'seguro', '--user', 'no-existe')
        self.assertTrue(User.objects.get(username='seguro').check_password('clave-propia-123'))
