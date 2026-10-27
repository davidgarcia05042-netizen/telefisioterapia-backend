from django.db import models


class DeferMovementManager(models.Manager):
    """
    Manager por defecto de Exercise y ExerciseResult: nunca carga `movement`.

    El movimiento pesa varios MB por fila (unos 3 MB de JSON y 20 MB ya
    decodificado para 60 s a 30 fps) y ningún listado lo devuelve, pero un
    queryset normal lo lee y decodifica igual. Con 600 ejercicios procesados,
    GET /routines/ necesitaba unos 12 GB y el backend (512 MB) murió por
    memoria el 2026-10-23.

    Al ser el manager por defecto, también lo usan los managers relacionados
    (rutina.exercises, entrenamiento.results) y los prefetch. Las FK directas
    (resultado.exercise) usan el manager base, que sí carga todo: los
    querysets que las recorren aplazan `exercise__movement` (therapy/querysets.py).

    Leer `instancia.movement` sigue funcionando: hace una consulta más, solo
    para esa fila. `.defer(None)` vuelve a cargar todos los campos.
    """

    def get_queryset(self):
        return super().get_queryset().defer('movement')
