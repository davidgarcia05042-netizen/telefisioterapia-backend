"""
Movimiento medido por el servicio, en la estructura del panel
(docs/DECISIONES_TECNICAS.md, decisión D4).

La estructura es la de `JSON_MOVIMIENTO_ESTRUCTURA.md` del panel (la que ya
consumen sus gráficas, exportaciones y visor 3D), más dos claves adicionales:

    {exercise_id, exercise_name, duration, source: "service",
     model: "mediapipe_heavy",
     frames: [{t, points: [[x, y, z, v] x 33], world_points: [[x, y, z] x 33],
               angles: {codename: ángulo 2D}, angles_3d: {codename: ángulo 3D}}]}

`world_points` y `angles_3d` son las adicionales. `angles` es el 2D (medición
clínica); `angles_3d` es visualización, no validada.
"""

SOURCE = 'service'
# El servicio fija el modelo con POSE_MODEL (MediaPipe Heavy en producción). Si
# se cambia allá, hay que reprocesar las referencias (ver docs/DECISIONES_TECNICAS.md).
MODEL = 'mediapipe_heavy'


def build_movement(exercise, frames, duration_s, codenames_by_joint):
    """
    Arma el movimiento.

    - `frames`: frames ya saneados del callback v2 (`t`, `landmarks`,
      `world_landmarks`, `angles_2d`, `angles_3d`).
    - `codenames_by_joint`: {índice en `joints`: [codename, ...]} de las
      articulaciones rastreadas que se mapearon a cada terna. Una terna
      compartida por dos articulaciones aparece con las dos.

    Los frames sin pose se omiten: el panel solo guarda frames con los 33
    puntos, y un `points` nulo rompería sus gráficas y el visor 3D. Un ángulo
    nulo se omite de `angles` (igual que en el análisis del navegador).
    """
    movement_frames = []
    for frame in frames:
        if frame['landmarks'] is None:
            continue
        angles, angles_3d = {}, {}
        for index, codenames in codenames_by_joint.items():
            angle_2d = _at(frame['angles_2d'], index)
            angle_3d = _at(frame['angles_3d'], index)
            for codename in codenames:
                if angle_2d is not None:
                    angles[codename] = angle_2d
                if angle_3d is not None:
                    angles_3d[codename] = angle_3d
        movement_frames.append({
            't': frame['t'],
            'points': frame['landmarks'],
            'world_points': frame['world_landmarks'],
            'angles': angles,
            'angles_3d': angles_3d,
        })

    return {
        'exercise_id': exercise.id,
        'exercise_name': exercise.name,
        'duration': duration_s,
        'source': SOURCE,
        'model': MODEL,
        'frames': movement_frames,
    }


def _at(values, index):
    return values[index] if values is not None and index < len(values) else None
