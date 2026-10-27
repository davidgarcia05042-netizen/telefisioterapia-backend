"""
Editar y borrar dificultades de un ejercicio (E5, docs/DECISIONES_TECNICAS.md).

La plataforma original solo permitía crearlas (post_difficulty). Aquí:
- editar cambia nombre, descripción y rangos; los rangos se reemplazan;
- borrar solo se permite si ninguna rutina programada la usa: si no, 409 con
  un mensaje que el panel muestra. `ScheduledTrainingDifficulty.difficulty`
  es DO_NOTHING, así que borrarla en uso dejaba la asignación apuntando a una
  fila inexistente (o fallaba la restricción de la base con un 500).

Las mediciones de los pacientes no guardan copia del rango: el resultado lo
compara con el rango vigente de la dificultad (/scheduled_training/diffs). Por
eso, si la dificultad está asignada, la edición puede cambiar nombre y
descripción pero no los rangos (409, como el borrado): cambiarlos alteraría el
historial de los resultados ya registrados. El panel ofrece duplicarla como
una dificultad nueva (F3, docs/DECISIONES_TECNICAS.md).
"""
from django.db import transaction

from telerehabilitation_API.therapy.models import DifficultyRange, ScheduledTrainingDifficulty


class DifficultyError(ValueError):
    """Datos inválidos: el mensaje se devuelve tal cual al panel (400)."""


def assignment_count(difficulty):
    """Cuántas rutinas programadas usan la dificultad."""
    return ScheduledTrainingDifficulty.objects.filter(difficulty=difficulty).count()


def _as_angle(value, label, joint):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise DifficultyError(f'El ángulo {label} de «{joint}» debe ser un número.')
    try:
        angle = float(value)
    except ValueError:
        raise DifficultyError(f'El ángulo {label} de «{joint}» debe ser un número.')
    if not 0 <= angle <= 360:
        raise DifficultyError(f'El ángulo {label} de «{joint}» debe estar entre 0 y 360 grados.')
    return angle


def parse_update(exercise, body):
    """Valida el cuerpo de la edición. Devuelve {name, description, ranges} o lanza DifficultyError."""
    if not hasattr(body, 'get'):
        raise DifficultyError('El cuerpo debe ser un objeto JSON.')
    name = body.get('name')
    if not isinstance(name, str) or not name.strip():
        raise DifficultyError('La dificultad debe tener un nombre.')
    description = body.get('description', '')
    if not isinstance(description, str):
        raise DifficultyError('La descripción debe ser texto.')
    entries = body.get('ranges')
    if not isinstance(entries, list):
        raise DifficultyError('"ranges" debe ser una lista.')

    tracked = {point.id: point for point in exercise.tracked_points.select_related('skeleton_point')}
    ranges, seen = [], set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise DifficultyError('Cada rango debe tener "point_tracked_id", "min_angle" y "max_angle".')
        point_id = entry.get('point_tracked_id')
        point = tracked.get(point_id) if isinstance(point_id, int) and not isinstance(point_id, bool) else None
        if point is None:
            raise DifficultyError(f'La articulación {point_id} no se rastrea en el ejercicio «{exercise.name}».')
        joint = point.skeleton_point.verbose if point.skeleton_point else f'articulación {point.id}'
        if point.id in seen:
            raise DifficultyError(f'«{joint}» tiene más de un rango.')
        seen.add(point.id)
        min_angle = _as_angle(entry.get('min_angle'), 'mínimo', joint)
        max_angle = _as_angle(entry.get('max_angle'), 'máximo', joint)
        if min_angle > max_angle:
            raise DifficultyError(f'En «{joint}» el ángulo mínimo es mayor que el máximo.')
        ranges.append((point, min_angle, max_angle))
    return {'name': name.strip(), 'description': description, 'ranges': ranges}


def _range_key(point_id, min_angle, max_angle):
    # Redondeo: los ángulos se guardan como float y llegan del panel con 2 decimales.
    return (point_id, round(float(min_angle), 6), round(float(max_angle), 6))


def ranges_changed(difficulty, data):
    """True si los rangos de `data` no son los que ya tiene la dificultad (sin importar el orden)."""
    current = {
        _range_key(r.point_tracked_id, r.min_angle, r.max_angle)
        for r in difficulty.ranges.all()
        if r.point_tracked_id is not None and r.min_angle is not None and r.max_angle is not None
    }
    wanted = {_range_key(point.id, min_angle, max_angle) for point, min_angle, max_angle in data['ranges']}
    return current != wanted


def _routines_text(count):
    return 'una rutina programada' if count == 1 else f'{count} rutinas programadas'


def update_conflict(difficulty, data):
    """Mensaje del 409 si la edición cambia los rangos de una dificultad asignada; None si se puede aplicar."""
    count = assignment_count(difficulty)
    if count and ranges_changed(difficulty, data):
        return (f'No se pueden cambiar los rangos de la dificultad «{difficulty.name}»: está asignada en '
                f'{_routines_text(count)} y sus resultados se comparan con estos rangos. Puedes cambiar el '
                'nombre y la descripción, o duplicarla como una nueva dificultad con los rangos que necesitas.')
    return None


def update_difficulty(difficulty, data):
    """Aplica la edición: todo o nada."""
    with transaction.atomic():
        difficulty.name = data['name']
        difficulty.description = data['description']
        difficulty.save(update_fields=['name', 'description'])
        difficulty.ranges.all().delete()
        DifficultyRange.objects.bulk_create([
            DifficultyRange(difficulty=difficulty, point_tracked=point, min_angle=min_angle, max_angle=max_angle)
            for point, min_angle, max_angle in data['ranges']
        ])


def delete_difficulty(difficulty):
    """
    Borra la dificultad y sus rangos. Devuelve None, o el mensaje del 409 si
    alguna rutina programada la usa (entonces no borra nada).
    """
    with transaction.atomic():
        count = assignment_count(difficulty)
        if count:
            return (f'No se puede eliminar la dificultad «{difficulty.name}»: está asignada en {_routines_text(count)}. '
                    'Puedes editarla, o crear otra y asignar esa en las nuevas rutinas.')
        # Los rangos apuntan a la dificultad con SET_NULL: se borran con ella
        # en lugar de quedar huérfanos.
        difficulty.ranges.all().delete()
        difficulty.delete()
    return None
