"""
Sesión: logout que revoca el token, tokens con vencimiento y login sin
escribir credenciales en los logs (hallazgos de seguridad de la plataforma
original, docs/DECISIONES_TECNICAS.md).
"""
import io
from contextlib import redirect_stdout
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

PASSWORD = 'clave-de-prueba-123'


class TokenSessionTests(TestCase):
    def setUp(self):
        call_command('loaddata', settings.BASE_DIR / 'initial_data.json', verbosity=0)
        self.user = User.objects.create_user('terapeuta', password=PASSWORD)
        Group.objects.get(name='Therapist').user_set.add(self.user)

    def login(self):
        return APIClient().post('/auth/users/login/', {'username': 'terapeuta', 'password': PASSWORD},
                                format='json')

    def client_with(self, key):
        api = APIClient()
        api.credentials(HTTP_AUTHORIZATION=f'Token {key}')
        return api

    def age(self, token, hours):
        Token.objects.filter(pk=token.pk).update(created=timezone.now() - timedelta(hours=hours))

    # --- Vencimiento ---

    def test_a_fresh_token_works(self):
        token = Token.objects.create(user=self.user)
        self.assertEqual(self.client_with(token.key).get('/patients').status_code, 200)

    def test_a_token_older_than_the_ttl_is_rejected_and_deleted(self):
        token = Token.objects.create(user=self.user)
        self.age(token, settings.TOKEN_TTL_HOURS + 1)
        response = self.client_with(token.key).get('/patients')
        self.assertEqual(response.status_code, 401)
        self.assertIn('venció', response.json()['detail'])
        self.assertFalse(Token.objects.filter(pk=token.pk).exists())

    def test_the_default_ttl_is_12_hours(self):
        self.assertEqual(settings.TOKEN_TTL_HOURS, 12)
        token = Token.objects.create(user=self.user)
        self.age(token, 11.9)
        self.assertEqual(self.client_with(token.key).get('/patients').status_code, 200)
        self.age(token, 12)
        self.assertEqual(self.client_with(token.key).get('/patients').status_code, 401)

    @override_settings(TOKEN_TTL_HOURS=1)
    def test_the_ttl_comes_from_settings(self):
        token = Token.objects.create(user=self.user)
        self.age(token, 2)
        self.assertEqual(self.client_with(token.key).get('/patients').status_code, 401)

    def test_an_unknown_token_is_still_401(self):
        self.assertEqual(self.client_with('no-existe').get('/patients').status_code, 401)

    # --- Login ---

    def test_login_reuses_a_valid_token(self):
        first = self.login().json()['access_token']
        self.assertEqual(self.login().json()['access_token'], first)

    def test_login_renews_an_expired_token(self):
        old = self.login().json()['access_token']
        self.age(Token.objects.get(key=old), settings.TOKEN_TTL_HOURS + 1)
        new = self.login().json()['access_token']
        self.assertNotEqual(new, old)
        self.assertFalse(Token.objects.filter(key=old).exists())
        self.assertEqual(self.client_with(new).get('/patients').status_code, 200)

    def test_login_reuses_a_token_with_more_than_the_renew_threshold_left(self):
        self.assertEqual(settings.TOKEN_RENEW_HOURS, 2)
        old = self.login().json()['access_token']
        self.age(Token.objects.get(key=old), settings.TOKEN_TTL_HOURS - 3)  # le quedan 3 h
        self.assertEqual(self.login().json()['access_token'], old)

    def test_login_renews_a_token_close_to_expiring(self):
        old = self.login().json()['access_token']
        self.age(Token.objects.get(key=old), settings.TOKEN_TTL_HOURS - 1.5)  # le queda 1,5 h
        new = self.login().json()['access_token']
        self.assertNotEqual(new, old)
        self.assertFalse(Token.objects.filter(key=old).exists())
        self.assertEqual(self.client_with(new).get('/patients').status_code, 200)

    @override_settings(TOKEN_RENEW_HOURS=4)
    def test_the_renew_threshold_comes_from_settings(self):
        old = self.login().json()['access_token']
        self.age(Token.objects.get(key=old), settings.TOKEN_TTL_HOURS - 3)
        self.assertNotEqual(self.login().json()['access_token'], old)

    def test_login_does_not_print_the_credentials(self):
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(self.login().status_code, 201)
            APIClient().post('/auth/users/login/', {'username': 'terapeuta', 'password': 'otra-clave-123'},
                             format='json')
        self.assertNotIn(PASSWORD, output.getvalue())
        self.assertNotIn('otra-clave-123', output.getvalue())

    # --- Logout ---

    def test_logout_revokes_the_token(self):
        key = self.login().json()['access_token']
        api = self.client_with(key)
        self.assertEqual(api.post('/auth/users/logout/').status_code, 204)
        self.assertFalse(Token.objects.filter(user=self.user).exists())
        self.assertEqual(api.get('/patients').status_code, 401)

    def test_logout_without_token_is_401(self):
        self.assertEqual(APIClient().post('/auth/users/logout/').status_code, 401)

    def test_login_after_logout_gives_a_new_token(self):
        old = self.login().json()['access_token']
        self.client_with(old).post('/auth/users/logout/')
        new = self.login().json()['access_token']
        self.assertNotEqual(new, old)
        self.assertEqual(self.client_with(new).get('/patients').status_code, 200)
