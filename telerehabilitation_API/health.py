from django.db import connection
from django.db.utils import DatabaseError
from django.http import JsonResponse


def health(request):
    """
    Health check de Render (healthCheckPath: /health). Vista de Django pura,
    fuera de DRF, para que no pase por autenticación por token.

    Comprueba también la conexión a la base: si la base no responde, devolver
    503 hace que Render no promueva un despliegue roto ni le mande tráfico.
    """
    try:
        connection.ensure_connection()
    except DatabaseError:
        return JsonResponse({"status": "error", "database": "unreachable"}, status=503)
    return JsonResponse({"status": "ok"})
