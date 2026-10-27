"""
Ayudas para las pruebas.

Desde M1 (docs/DECISIONES_TECNICAS.md) los endpoints exigen un rol: un usuario sin grupo ya
no pasa. Las pruebas del panel usan un terapeuta.
"""
from django.contrib.auth.models import Group, User


def user_in_group(group, username=None, password='x-pass-123'):
    """Usuario del grupo `group` (Admin, Therapist o Patient); crea el grupo si falta."""
    username = username or f'{group.lower()}-{User.objects.count() + 1}'
    user = User.objects.create_user(username, password=password)
    Group.objects.get_or_create(name=group)[0].user_set.add(user)
    return user


def therapist_user(username='terapeuta'):
    return user_in_group('Therapist', username)
