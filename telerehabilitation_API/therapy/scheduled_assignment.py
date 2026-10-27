"""
Asignación de una rutina a un paciente (POST /scheduled_training).

La plataforma original leía `diff['diff']` de cada dificultad sin validarla
(500 por KeyError si faltaba) y creaba el entrenamiento y sus dificultades
sin transacción, así que un fallo a mitad dejaba la asignación a medias
(hallazgo de la plataforma original, docs/DECISIONES_TECNICAS.md). Aquí se valida todo antes de
escribir y la vista crea todo dentro de transaction.atomic().
"""
from datetime import datetime

from django.db import transaction

from telerehabilitation_API.therapy.models import (
    Exercise, ExerciseDifficulty, Routine, ScheduledTraining, ScheduledTrainingDifficulty, TherapyPatient,
)

START_TIME_FORMAT = '%d/%m/%Y %H:%M:%S'
BOGOTA_OFFSET = '-0500'


class AssignmentError(ValueError):
    """Datos inválidos: el mensaje se devuelve tal cual al panel (400)."""


def _as_id(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value)
    return None


def _parse_start_time(value):
    try:
        return datetime.strptime(f'{value}:{BOGOTA_OFFSET}', f'{START_TIME_FORMAT}:%z')
    except (TypeError, ValueError):
        raise AssignmentError('La fecha de inicio debe tener el formato DD/MM/AAAA HH:MM:SS.')


def _parse_difficulties(entries, routine):
    """[(ejercicio, dificultad)] validados. Una lista vacía es válida (T3: dificultad opcional)."""
    if entries is None:
        return []
    if not isinstance(entries, list):
        raise AssignmentError('"difficulties" debe ser una lista.')
    exercises = {exercise.id: exercise for exercise in Exercise.objects.filter(routine=routine)}
    pairs, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise AssignmentError('Cada dificultad debe tener "exercise_id" y "diff".')
        exercise_id = _as_id(entry.get('exercise_id'))
        if exercise_id is None:
            raise AssignmentError('Cada dificultad debe indicar su ejercicio ("exercise_id").')
        exercise = exercises.get(exercise_id)
        if exercise is None:
            raise AssignmentError(f'El ejercicio {exercise_id} no pertenece a la rutina «{routine.name}».')
        if exercise_id in seen:
            raise AssignmentError(f'El ejercicio «{exercise.name}» tiene más de una dificultad.')
        seen.add(exercise_id)
        difficulty_id = _as_id(entry.get('diff'))
        if difficulty_id is None:
            raise AssignmentError(f'Falta la dificultad ("diff") del ejercicio «{exercise.name}».')
        difficulty = ExerciseDifficulty.objects.filter(pk=difficulty_id, exercise_id=exercise_id).first()
        if difficulty is None:
            raise AssignmentError(
                f'La dificultad {difficulty_id} no pertenece al ejercicio «{exercise.name}».'
            )
        pairs.append((exercise, difficulty))
    return pairs


def parse_assignment(body):
    """Valida el cuerpo del POST. Devuelve un dict listo para `create_assignment` o lanza AssignmentError."""
    if not hasattr(body, 'get'):
        raise AssignmentError('El cuerpo debe ser un objeto JSON.')
    routine_id = _as_id(body.get('routine'))
    routine = Routine.objects.filter(pk=routine_id).first() if routine_id is not None else None
    if routine is None:
        raise AssignmentError('La rutina no existe.')
    therapy_patient_id = _as_id(body.get('therapy_patient'))
    therapy_patient = (TherapyPatient.objects.filter(pk=therapy_patient_id).first()
                       if therapy_patient_id is not None else None)
    if therapy_patient is None:
        raise AssignmentError('El paciente no está inscrito en la terapia.')
    return {
        'routine': routine,
        'therapy_patient': therapy_patient,
        'start_time': _parse_start_time(body.get('start_time')),
        'difficulties': _parse_difficulties(body.get('difficulties'), routine),
    }


def create_assignment(data):
    """Crea el entrenamiento programado y sus dificultades: todo o nada."""
    with transaction.atomic():
        scheduled_training = ScheduledTraining.objects.create(
            routine=data['routine'],
            therapy_patient=data['therapy_patient'],
            start_time=data['start_time'],
        )
        ScheduledTrainingDifficulty.objects.bulk_create([
            ScheduledTrainingDifficulty(difficulty=difficulty, exercise=exercise,
                                        scheduled_training=scheduled_training)
            for exercise, difficulty in data['difficulties']
        ])
    return scheduled_training
