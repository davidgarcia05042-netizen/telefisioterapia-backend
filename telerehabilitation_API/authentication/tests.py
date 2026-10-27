from itertools import count

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from telerehabilitation_API.authentication.models import Admin, Patient, Therapist

_usernames = count()


class UserEndpointPermissionTests(TestCase):
    """
    /admins, /therapists y /patients: antes no exigían autenticación y
    cualquiera podía crearse una cuenta del grupo "Admin".
    """

    def setUp(self):
        # Los grupos (con sus ids y permisos) los crea el fixture, como en producción.
        call_command('loaddata', settings.BASE_DIR / 'initial_data.json', verbosity=0)

    def client_for(self, *groups, superuser=False):
        """Cliente con un token real (pasa por TokenAuthentication)."""
        username = f'user{next(_usernames)}'
        if superuser:
            user = User.objects.create_superuser(username, f'{username}@example.com', 'x-pass-123')
        else:
            user = User.objects.create_user(username, f'{username}@example.com', 'x-pass-123')
        for name in groups:
            Group.objects.get(name=name).user_set.add(user)
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=user).key}')
        return api

    def new_user_payload(self):
        username = f'nuevo{next(_usernames)}'
        return {'username': username, 'email': f'{username}@example.com',
                'first_name': 'Nombre', 'last_name': 'Apellido'}

    def test_without_token_is_401(self):
        anonymous = APIClient()
        for endpoint in ('/admins', '/therapists', '/patients'):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(anonymous.get(endpoint).status_code, 401)
                response = anonymous.post(endpoint, self.new_user_payload(), format='json')
                self.assertEqual(response.status_code, 401)
                self.assertIn('WWW-Authenticate', response)
        self.assertFalse(Admin.objects.exists())

    def test_create_users_only_admin(self):
        admin = self.client_for('Admin')
        therapist = self.client_for('Therapist')
        patient = self.client_for('Patient')
        cases = [('/admins', Admin, 'Admin'), ('/therapists', Therapist, 'Therapist'),
                 ('/patients', Patient, 'Patient')]
        for endpoint, model, group in cases:
            with self.subTest(endpoint=endpoint):
                for denied in (therapist, patient):
                    payload = self.new_user_payload()
                    self.assertEqual(denied.post(endpoint, payload, format='json').status_code, 403)
                    self.assertFalse(User.objects.filter(username=payload['username']).exists())

                payload = self.new_user_payload()
                self.assertEqual(admin.post(endpoint, payload, format='json').status_code, 201)
                created = User.objects.get(username=payload['username'])
                self.assertTrue(created.groups.filter(name=group).exists())
                self.assertTrue(model.objects.filter(user=created).exists())

    def test_list_patients_and_therapists_admin_or_therapist(self):
        allowed = (self.client_for('Admin'), self.client_for('Therapist'))
        patient = self.client_for('Patient')
        for endpoint in ('/patients', '/therapists'):
            with self.subTest(endpoint=endpoint):
                for api in allowed:
                    self.assertEqual(api.get(endpoint).status_code, 200)
                self.assertEqual(patient.get(endpoint).status_code, 403)

    def test_list_admins_only_admin(self):
        self.assertEqual(self.client_for('Admin').get('/admins').status_code, 200)
        self.assertEqual(self.client_for('Therapist').get('/admins').status_code, 403)
        self.assertEqual(self.client_for('Patient').get('/admins').status_code, 403)

    def test_role_comes_from_the_group_not_from_superuser_flag(self):
        # Solo el grupo "Admin" da el rol (create_superuser.py ya lo asigna).
        bare_superuser = self.client_for(superuser=True)
        self.assertEqual(bare_superuser.get('/admins').status_code, 403)
        self.assertEqual(bare_superuser.post('/admins', self.new_user_payload(), format='json').status_code, 403)
