#!/bin/sh
set -e

# No se corre makemigrations: las migraciones se versionan en el repo. Generarlas
# dentro del contenedor producía un esquema distinto al del código revisado.
echo "📌 Ejecutando migraciones..."
python manage.py migrate --noinput

echo "📌 Cargando datos iniciales..."
python manage.py loaddata initial_data.json || echo "⚠️ No se pudieron cargar los fixtures (puede que ya estén cargados)"

echo "📌 Creando superusuario si está configurado..."
python create_superuser.py || echo "⚠️ No se creó el superusuario"

# exec: gunicorn reemplaza al shell y recibe directamente las señales de Render
# (SIGTERM al redesplegar), en vez de que las absorba sh.
# --max-requests: cada worker se reemplaza tras 1000 a 1100 peticiones (el
# jitter evita que los dos lo hagan a la vez). Es una red de seguridad: devuelve
# al sistema la memoria que Python y glibc retienen; en M5 (2026-10-19) se
# acumuló hasta pasar los 512 MB del plan starter. Con 200 se reciclaba cada
# pocos minutos, porque cuentan también los sondeos (panel cada 5 s, app cada
# 10 s mientras procesa).
# --graceful-timeout 90: al recibir SIGTERM, el worker tiene 90 s para terminar
# lo que tenga en curso, incluido un envío de video al servicio (el hilo no es
# daemon, ver processing/client.py). Al reciclarse por --max-requests también
# espera al envío; ahí el tope lo pone --timeout (120 s).
echo "🚀 Iniciando gunicorn..."
exec gunicorn telerehabilitation_API.wsgi --bind "0.0.0.0:${PORT:-8000}" --workers 2 --threads 4 --timeout 120 \
    --graceful-timeout 90 --max-requests 1000 --max-requests-jitter 100
