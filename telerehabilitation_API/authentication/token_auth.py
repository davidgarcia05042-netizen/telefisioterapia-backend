"""
Tokens con vencimiento.

`rest_framework.authtoken` no vence ni revoca tokens: el de la plataforma
original valía para siempre (hallazgo de seguridad, docs/DECISIONES_TECNICAS.md). Acá un token
vale TOKEN_TTL_HOURS desde que se creó; pasado ese plazo se borra y la
solicitud recibe 401, así que el panel lleva al login ("Tu sesión expiró").

El plazo cuenta desde `Token.created` y no se extiende con el uso: una sesión
olvidada abierta no vive más que eso. Para que quien inicia sesión no reciba
un token a punto de vencer, el login lo renueva si le quedan menos de
TOKEN_RENEW_HOURS.
"""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework.authentication import TokenAuthentication
from rest_framework.authtoken.models import Token
from rest_framework.exceptions import AuthenticationFailed

EXPIRED_TOKEN_MESSAGE = 'El token venció. Inicia sesión de nuevo.'


def token_remaining(token):
    """Tiempo que le queda al token (negativo si ya venció)."""
    return token.created + timedelta(hours=settings.TOKEN_TTL_HOURS) - timezone.now()


def token_expired(token):
    return token_remaining(token) <= timedelta(0)


def valid_token_for(user):
    """
    El token del usuario para el login. `authtoken` guarda uno por usuario: si
    el que existe venció o le quedan menos de TOKEN_RENEW_HOURS, se borra y se
    crea otro; si no, se reutiliza (como hacía el login original con
    get_or_create).

    Renovar borra el token anterior, así que cierra la sesión en los otros
    navegadores del usuario: por eso solo se hace cerca del vencimiento y no en
    cada login.
    """
    token, created = Token.objects.get_or_create(user=user)
    if not created and token_remaining(token) < timedelta(hours=settings.TOKEN_RENEW_HOURS):
        token.delete()
        token = Token.objects.create(user=user)
    return token


class ExpiringTokenAuthentication(TokenAuthentication):
    """TokenAuthentication que rechaza (y borra) los tokens vencidos."""

    def authenticate_credentials(self, key):
        user, token = super().authenticate_credentials(key)
        if token_expired(token):
            token.delete()
            raise AuthenticationFailed(EXPIRED_TOKEN_MESSAGE)
        return user, token
