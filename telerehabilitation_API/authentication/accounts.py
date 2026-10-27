"""
Creación de cuentas con contraseña temporal (B1/DM4, docs/DECISIONES_TECNICAS.md).

El original creaba a todos los usuarios (pacientes, terapeutas y
administradores) con la contraseña '123456789': cualquiera que supiera un
nombre de usuario podía entrar. Ahora cada cuenta nueva recibe una contraseña
temporal aleatoria que se devuelve UNA sola vez en la respuesta del POST; el
panel la muestra con el aviso de copiarla. No se guarda en claro ni se escribe
en los logs.
"""
import secrets

from django.contrib.auth.models import Group, User
from django.db import transaction
from rest_framework.response import Response

# Sin caracteres que se confunden al dictarlos o copiarlos a mano (0/O, 1/l/I).
_LETTERS = 'abcdefghijkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ'
_DIGITS = '23456789'
TEMPORARY_PASSWORD_LENGTH = 12
REQUIRED_FIELDS = (('username', 'el usuario'), ('email', 'el correo'),
                   ('first_name', 'el nombre'), ('last_name', 'el apellido'))
LEGACY_DEFAULT_PASSWORD = '123456789'


class AccountError(ValueError):
    """Datos inválidos: el mensaje se devuelve tal cual al panel (400)."""


def generate_temporary_password(length=TEMPORARY_PASSWORD_LENGTH):
    """Contraseña aleatoria (módulo secrets) con al menos una letra y un dígito."""
    alphabet = _LETTERS + _DIGITS
    while True:
        password = ''.join(secrets.choice(alphabet) for _ in range(length))
        if any(c in _DIGITS for c in password) and any(c in _LETTERS for c in password):
            return password


def _clean(data):
    if not hasattr(data, 'get'):
        raise AccountError('El cuerpo debe ser un objeto JSON.')
    cleaned = {}
    for field, label in REQUIRED_FIELDS:
        value = data.get(field)
        if not isinstance(value, str) or not value.strip():
            raise AccountError(f'Falta {label}.')
        cleaned[field] = value.strip()
    if User.objects.filter(username__iexact=cleaned['username']).exists():
        raise AccountError(f'Ya existe un usuario «{cleaned["username"]}».')
    return cleaned


def create_account(data, group_name, profile_model, **profile_fields):
    """
    Crea el usuario, lo agrega al grupo y crea su perfil (Patient, Therapist o
    Admin), todo o nada. Devuelve (usuario, contraseña temporal).
    """
    cleaned = _clean(data)
    password = generate_temporary_password()
    with transaction.atomic():
        user = User.objects.create_user(cleaned['username'], cleaned['email'], password,
                                        first_name=cleaned['first_name'], last_name=cleaned['last_name'])
        Group.objects.get(name=group_name).user_set.add(user)
        profile_model.objects.create(user=user, **profile_fields)
    return user, password


def created_response(user, password):
    """
    201 con la contraseña temporal: va solo aquí, una vez. Cache-Control
    no-store para que ningún intermediario ni el navegador la guarden.
    """
    response = Response({'id': user.id, 'username': user.username, 'temporary_password': password}, status=201)
    response['Cache-Control'] = 'no-store'
    return response
