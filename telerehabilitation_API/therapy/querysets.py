"""
Querysets de los listados con todo lo que sus serializers anidados necesitan.

Sin esto cada ejercicio pedía sus dificultades, cada dificultad sus rangos y
cada rango su articulación y su punto del esqueleto: con una rutina de 608
ejercicios, GET /routines/ hacía unas 3800 consultas y GET /scheduled_training
de la app, unas 11 000. Con los prefetch, el número de consultas ya no depende
de cuántos ejercicios haya.

El movimiento de Exercise y ExerciseResult no se carga en ninguno: el manager
por defecto lo aplaza (models/managers.py), y donde se llega al ejercicio por
una FK directa (resultado.exercise, que usa el manager base) se aplaza aquí
con `exercise__movement`.
"""
from django.db.models import Prefetch

from telerehabilitation_API.therapy.models import ExerciseResult


def exercise_lookups(prefix=''):
    """Lo que anida ExerciseSerializer: dificultades > rangos > articulación > punto."""
    return [f'{prefix}difficulties__ranges__point_tracked__skeleton_point']


def routine_lookups(prefix=''):
    """Lo que anida RoutineSerializer: sus ejercicios y la terapia con los enlaces a sus rutinas."""
    return [f'{prefix}therapy__routines', *exercise_lookups(f'{prefix}exercises__')]


def with_routine_details(queryset):
    """Rutinas para RoutineSerializer."""
    return queryset.select_related('therapy').prefetch_related(*routine_lookups())


def with_schedule_details(queryset):
    """Rutinas programadas cuya rutina se serializa con RoutineSerializer."""
    return queryset.select_related('routine__therapy').prefetch_related(*routine_lookups('routine__'))


def result_lookups(prefix=''):
    return [*exercise_lookups(f'{prefix}exercise__'), f'{prefix}points__point_tracked__skeleton_point']


def with_result_details(queryset):
    """
    Resultados para ExerciseResultSerializer. `exercise` es una FK directa: se
    trae con select_related y sin su movimiento (el del resultado ya lo aplaza
    el manager).
    """
    return (queryset.select_related('exercise').defer('exercise__movement')
            .prefetch_related(*result_lookups()))


def with_training_details(queryset):
    """Sesiones para TrainingSerializer: la rutina programada completa y sus resultados."""
    return (queryset.select_related('schedule_training__routine__therapy',
                                    'schedule_training__therapy_patient__therapy')
            .prefetch_related(*routine_lookups('schedule_training__routine__'),
                              Prefetch('results', queryset=with_result_details(ExerciseResult.objects.all()))))
