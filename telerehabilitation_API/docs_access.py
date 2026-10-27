"""
Acceso a la documentación de la API (swagger/ y redoc/).

La plataforma original la publicaba para cualquiera: el esquema completo de
endpoints, parámetros y cuerpos. Ahora solo responde con DEBUG=True (local) o a
un administrador autenticado, ya sea con la sesión del sitio de administración
de Django (lo normal al abrirla en el navegador) o con el token de un usuario
del grupo Admin. En cualquier otro caso responde 404: no se revela que existe.
"""
from functools import wraps

from django.conf import settings
from django.http import Http404
from rest_framework.exceptions import AuthenticationFailed

from telerehabilitation_API.authentication.permissions import ADMIN, in_any_group
from telerehabilitation_API.authentication.token_auth import ExpiringTokenAuthentication


def _is_admin(user):
    return bool(user and user.is_authenticated and (user.is_superuser or in_any_group(user, (ADMIN,))))


def _token_user(request):
    try:
        authenticated = ExpiringTokenAuthentication().authenticate(request)
    except AuthenticationFailed:
        return None
    return authenticated[0] if authenticated else None


def admin_only_docs(view):
    """Envuelve una vista de documentación: DEBUG o administrador; si no, 404."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if settings.DEBUG or _is_admin(getattr(request, 'user', None)) or _is_admin(_token_user(request)):
            return view(request, *args, **kwargs)
        raise Http404()

    return wrapped
