"""
Siembra los 33 puntos del esqueleto de MediaPipe Pose y los vecinos por defecto
de 12 articulaciones (docs/DECISIONES_TECNICAS.md, 2.2).

El id de cada SkeletonPoint ES el índice del landmark de MediaPipe (0-32): el
panel calcula los ángulos con landmark[point.id], landmark[point.left_point] y
landmark[point.right_point] (backgroundAnalysisService.js), y el Servicio de
Procesamiento recibe esos mismos índices en `points`.

En este modelo "left_point"/"right_point" significa "los dos vecinos" del
vértice, no el lado anatómico: el ángulo se mide en el vértice entre ambos.

Es idempotente (update_or_create) y va en dos pasadas porque los vecinos son FK
a la misma tabla: primero deben existir los 33 puntos.
"""
from django.db import migrations

# (id, codename, verbose) según JSON_MOVIMIENTO_ESTRUCTURA.md del panel.
SKELETON_POINTS = [
    (0, 'NOSE', 'Nariz'),
    (1, 'LEFT_EYE_INNER', 'Ojo izquierdo (esquina interna)'),
    (2, 'LEFT_EYE', 'Ojo izquierdo (centro)'),
    (3, 'LEFT_EYE_OUTER', 'Ojo izquierdo (esquina externa)'),
    (4, 'RIGHT_EYE_INNER', 'Ojo derecho (esquina interna)'),
    (5, 'RIGHT_EYE', 'Ojo derecho (centro)'),
    (6, 'RIGHT_EYE_OUTER', 'Ojo derecho (esquina externa)'),
    (7, 'LEFT_EAR', 'Oreja izquierda'),
    (8, 'RIGHT_EAR', 'Oreja derecha'),
    (9, 'MOUTH_LEFT', 'Comisura izquierda de la boca'),
    (10, 'MOUTH_RIGHT', 'Comisura derecha de la boca'),
    (11, 'LEFT_SHOULDER', 'Hombro izquierdo'),
    (12, 'RIGHT_SHOULDER', 'Hombro derecho'),
    (13, 'LEFT_ELBOW', 'Codo izquierdo'),
    (14, 'RIGHT_ELBOW', 'Codo derecho'),
    (15, 'LEFT_WRIST', 'Muñeca izquierda'),
    (16, 'RIGHT_WRIST', 'Muñeca derecha'),
    (17, 'LEFT_PINKY', 'Meñique izquierdo'),
    (18, 'RIGHT_PINKY', 'Meñique derecho'),
    (19, 'LEFT_INDEX', 'Índice izquierdo (mano)'),
    (20, 'RIGHT_INDEX', 'Índice derecho (mano)'),
    (21, 'LEFT_THUMB', 'Pulgar izquierdo'),
    (22, 'RIGHT_THUMB', 'Pulgar derecho'),
    (23, 'LEFT_HIP', 'Cadera izquierda'),
    (24, 'RIGHT_HIP', 'Cadera derecha'),
    (25, 'LEFT_KNEE', 'Rodilla izquierda'),
    (26, 'RIGHT_KNEE', 'Rodilla derecha'),
    (27, 'LEFT_ANKLE', 'Tobillo izquierdo'),
    (28, 'RIGHT_ANKLE', 'Tobillo derecho'),
    (29, 'LEFT_HEEL', 'Talón izquierdo'),
    (30, 'RIGHT_HEEL', 'Talón derecho'),
    (31, 'LEFT_FOOT_INDEX', 'Índice del pie izquierdo'),
    (32, 'RIGHT_FOOT_INDEX', 'Índice del pie derecho'),
]

# vértice: (left_point, right_point). El resto de puntos queda sin vecinos.
DEFAULT_NEIGHBORS = {
    11: (13, 23),  # hombro izq.: codo y cadera
    12: (14, 24),
    13: (11, 15),  # codo izq.: hombro y muñeca
    14: (12, 16),
    15: (13, 19),  # muñeca izq.: codo e índice de la mano
    16: (14, 20),
    23: (11, 25),  # cadera izq.: hombro y rodilla
    24: (12, 26),
    25: (23, 27),  # rodilla izq.: cadera y tobillo
    26: (24, 28),
    27: (25, 31),  # tobillo izq.: rodilla e índice del pie
    28: (26, 32),
}


def seed_skeleton_points(apps, schema_editor):
    SkeletonPoint = apps.get_model('therapy', 'SkeletonPoint')

    for point_id, codename, verbose in SKELETON_POINTS:
        SkeletonPoint.objects.update_or_create(
            id=point_id, defaults={'codename': codename, 'verbose': verbose},
        )

    for vertex_id, (left_id, right_id) in DEFAULT_NEIGHBORS.items():
        SkeletonPoint.objects.filter(id=vertex_id).update(
            left_point_id=left_id, right_point_id=right_id,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('therapy', '0005_exerciseresultpoint_angle_series_error'),
    ]

    # Reversa sin efecto: borrar los puntos dejaría huérfanos los ejercicios
    # que ya los referencian.
    operations = [
        migrations.RunPython(seed_skeleton_points, migrations.RunPython.noop),
    ]
