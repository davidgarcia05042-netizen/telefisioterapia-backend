"""
Permisos por rol (M1, docs/DECISIONES_TECNICAS.md).

El backend original no definía DEFAULT_PERMISSION_CLASSES, así que toda vista
sin permiso declarado quedaba abierta. Ahora el valor por defecto es
IsAuthenticated (settings.py) y cada vista declara el rol que la usa:
- IsAdmin: administración de usuarios administradores.
- IsAdminOrTherapist: todo lo del panel.
- IsAdminTherapistOrPatient: los endpoints que también usa la app del
  paciente. Este permiso solo verifica el rol; que el recurso sea del paciente
  lo verifica cada vista (therapy/ownership.py) y, si no es suyo, responde 404.

Sin token, DRF responde 401 (TokenAuthentication declara WWW-Authenticate);
con token pero sin el grupo, 403.
"""
from rest_framework import permissions

ADMIN = 'Admin'
THERAPIST = 'Therapist'
PATIENT = 'Patient'
STAFF_GROUPS = (ADMIN, THERAPIST)


def in_any_group(user, group_names):
    return bool(user and user.is_authenticated and user.groups.filter(name__in=group_names).exists())


def is_staff_member(user):
    """Administrador o terapeuta: quien usa el panel."""
    return in_any_group(user, STAFF_GROUPS)


class IsAdmin(permissions.BasePermission):
    """Solo usuarios del grupo "Admin", en todos los métodos (lectura incluida)."""

    def has_permission(self, request, view):
        return in_any_group(request.user, (ADMIN,))


class IsAdminOrTherapist(permissions.BasePermission):
    """
    Solo usuarios de los grupos "Admin" o "Therapist", en todos los métodos.
    La lectura no queda abierta: terapias, rutinas, pacientes y resultados
    tienen datos clínicos o personales.
    """

    def has_permission(self, request, view):
        return is_staff_member(request.user)


class IsAdminTherapistOrPatient(permissions.BasePermission):
    """Admin, Therapist o Patient. La vista limita al paciente a sus propios datos."""

    def has_permission(self, request, view):
        return in_any_group(request.user, STAFF_GROUPS + (PATIENT,))
