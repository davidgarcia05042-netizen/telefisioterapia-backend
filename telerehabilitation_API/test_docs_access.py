"""
swagger/ y redoc/ solo con DEBUG=True o para un administrador; si no, 404
(docs_access.py).
"""
from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from rest_framework.authtoken.models import Token

from telerehabilitation_API.test_utils import user_in_group

DOCS = ('/swagger/', '/redoc/', '/swagger/?format=openapi')


@override_settings(DEBUG=False)
class DocsAccessTests(TestCase):
    def token_client(self, user):
        return Client(HTTP_AUTHORIZATION=f'Token {Token.objects.create(user=user).key}')

    def assert_status(self, client, expected):
        for url in DOCS:
            self.assertEqual(client.get(url).status_code, expected, url)

    def test_without_session_is_404(self):
        self.assert_status(Client(), 404)

    def test_patient_and_therapist_get_404(self):
        self.assert_status(self.token_client(user_in_group('Patient')), 404)
        self.assert_status(self.token_client(user_in_group('Therapist')), 404)

    def test_an_invalid_token_is_404(self):
        self.assert_status(Client(HTTP_AUTHORIZATION='Token no-existe'), 404)

    def test_admin_with_token_can_read(self):
        self.assert_status(self.token_client(user_in_group('Admin')), 200)

    def test_superuser_with_django_admin_session_can_read(self):
        client = Client()
        client.force_login(User.objects.create_superuser('root', 'root@test.local', 'clave-root-123'))
        self.assert_status(client, 200)

    def test_a_non_admin_session_is_404(self):
        client = Client()
        client.force_login(user_in_group('Therapist'))
        self.assert_status(client, 404)

    @override_settings(DEBUG=True)
    def test_debug_serves_it_to_everyone(self):
        self.assert_status(Client(), 200)
