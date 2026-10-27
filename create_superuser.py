# create_superuser.py
"""
Crea el superusuario solo si vienen DJANGO_SUPERUSER_USERNAME y
DJANGO_SUPERUSER_PASSWORD. No hay valores por defecto: la versión original
traía la contraseña "123456789" y la imprimía en los logs del despliegue.

Además lo deja como administrador de la plataforma (grupo "Admin" y fila en
authentication.Admin), igual que AdminViewSet.post: el panel solo deja entrar
a usuarios de los grupos "Admin" o "Therapist" (login.vue), y un superusuario
sin grupo quedaba fuera.

Es idempotente y corre en cada arranque (entrypoint.sh, después de loaddata,
que crea el grupo): también completa un superusuario creado antes de este
cambio. Nunca cambia la contraseña de un usuario existente.
"""
import os

import django
import environ


def ensure_superuser(username, email, password):
    """Crea el superusuario si falta y garantiza su grupo "Admin" y su fila Admin."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Group

    from telerehabilitation_API.authentication.models import Admin

    User = get_user_model()

    user = User.objects.filter(username=username).first()
    if user is None:
        user = User.objects.create_superuser(username, email, password)
        print(f"Superuser '{username}' creado.")
    elif not user.is_superuser:
        # Un usuario común con el mismo nombre no se promueve a administrador
        # por coincidir con una variable de entorno.
        print(f"El usuario '{username}' existe y no es superusuario: no se modifica.")
        return user
    else:
        print(f"El superuser '{username}' ya existe.")

    # Sin get_or_create: el grupo lo crea initial_data.json con id y permisos
    # fijos; crearlo aquí vacío haría fallar después ese fixture (nombre único).
    admin_group = Group.objects.filter(name="Admin").first()
    if admin_group is None:
        print("⚠️ No existe el grupo 'Admin' (¿falló loaddata?): el superusuario queda sin rol en el panel.")
    else:
        admin_group.user_set.add(user)
    Admin.objects.get_or_create(user=user)
    return user


def main():
    environ.Env.read_env()
    env = environ.Env()

    username = env("DJANGO_SUPERUSER_USERNAME", default="")
    email = env("DJANGO_SUPERUSER_EMAIL", default="")
    password = env("DJANGO_SUPERUSER_PASSWORD", default="")

    if not username or not password:
        print("DJANGO_SUPERUSER_USERNAME/DJANGO_SUPERUSER_PASSWORD no definidas: no se crea superusuario.")
        return

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "telerehabilitation_API.settings")
    django.setup()
    ensure_superuser(username, email, password)


if __name__ == "__main__":
    main()
