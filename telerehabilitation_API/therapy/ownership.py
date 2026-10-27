"""
Lo que es "del paciente" (M1, docs/DECISIONES_TECNICAS.md).

La app del paciente usa algunos endpoints del panel, pero solo sobre sus
propios datos: sus rutinas programadas, las sesiones de esas rutinas y los
resultados de esas sesiones. Administradores y terapeutas ven todo.

Si el recurso no es del paciente, la vista responde 404 (no 403): no se revela
que existe.
"""
from telerehabilitation_API.authentication.permissions import is_staff_member
from telerehabilitation_API.therapy.models import ExerciseResult, ScheduledTraining, Training


def scheduled_trainings_for(user):
    """Rutinas programadas que el usuario puede ver."""
    queryset = ScheduledTraining.objects.all()
    return queryset if is_staff_member(user) else queryset.filter(therapy_patient__patient__user=user)


def trainings_for(user):
    """Sesiones de entrenamiento que el usuario puede ver o modificar."""
    queryset = Training.objects.all()
    return queryset if is_staff_member(user) else queryset.filter(
        schedule_training__therapy_patient__patient__user=user)


def exercise_results_for(user):
    """Resultados que el usuario puede ver."""
    queryset = ExerciseResult.objects.all()
    return queryset if is_staff_member(user) else queryset.filter(
        training__schedule_training__therapy_patient__patient__user=user)


def as_id(value):
    """Un id no numérico cuenta como inexistente (404/400) en vez de producir un 500."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
