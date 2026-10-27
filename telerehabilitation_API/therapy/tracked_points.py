"""
Articulaciones rastreadas de un ejercicio al subir un video de referencia.

Se actualizan EN SITIO por `skeleton_point_id`, no se borran y recrean: los
rangos de las dificultades (`DifficultyRange.point_tracked`) y las mediciones
de los pacientes (`ExerciseResultPoint.point_tracked`) apuntan a estas filas
con SET_NULL, así que recrearlas dejaba los rangos huérfanos al cambiar el
video de un ejercicio con dificultades.
"""
import json

from telerehabilitation_API.therapy.models import DifficultyRange, ExerciseSkeletonPointTracked


def _optional_int(value):
    return int(value) if value is not None else None


def parse_points(points_data):
    """
    {skeleton_point_id: (left_point_id, right_point_id)} en el orden pedido.

    Acepta el formato nuevo (lista JSON de objetos con adyacentes
    personalizados: [{"skeleton_point_id": 11, "left_point_id": 13,
    "right_point_id": 23}, ...]) y el legacy (IDs separados por comas
    "11,13,25"). Si una articulación se repite, vale la primera.
    """
    # Si es un string que parece JSON list, lo parseamos
    if isinstance(points_data, str) and points_data.strip().startswith('['):
        try:
            points_data = json.loads(points_data)
        except ValueError:
            pass

    requested = {}
    if isinstance(points_data, list):
        for pt in points_data:
            requested.setdefault(
                int(pt['skeleton_point_id']),
                (_optional_int(pt.get('left_point_id')), _optional_int(pt.get('right_point_id'))),
            )
    else:
        for point in str(points_data).split(','):
            if point.strip().isdigit():
                requested.setdefault(int(point), (None, None))
    return requested


def sync_tracked_points(exercise, requested):
    """
    Deja las articulaciones del ejercicio iguales a `requested`:
    - las que siguen conservan su fila (y con ella rangos y mediciones de
      pacientes), con los vecinos nuevos y sin el mínimo/máximo del video
      anterior, que ya no corresponde;
    - las nuevas se crean;
    - solo las quitadas se borran.

    Llamar dentro de la transacción de la subida.
    """
    kept = {}
    for tracked in ExerciseSkeletonPointTracked.objects.filter(exercise=exercise).order_by('id'):
        skeleton_point_id = tracked.skeleton_point_id
        if skeleton_point_id in kept:
            # Duplicado heredado de datos viejos: sus rangos pasan a la fila que queda.
            DifficultyRange.objects.filter(point_tracked=tracked).update(point_tracked=kept[skeleton_point_id])
            tracked.delete()
        elif skeleton_point_id not in requested:
            tracked.delete()
        else:
            kept[skeleton_point_id] = tracked

    for skeleton_point_id, (left_point_id, right_point_id) in requested.items():
        tracked = kept.get(skeleton_point_id) or ExerciseSkeletonPointTracked(
            exercise=exercise, skeleton_point_id=skeleton_point_id,
        )
        tracked.left_point_id = left_point_id
        tracked.right_point_id = right_point_id
        tracked.max_angle = None
        tracked.min_angle = None
        tracked.save()
