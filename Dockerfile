FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /usr/api

# psycopg2 (no la variante -binary) compila contra libpq: necesita los
# encabezados de libpq y un compilador.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /usr/api
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . /usr/api

# collectstatic importa settings.py, que exige SECRET_KEY y DATABASE_URL.
# Se pasan valores desechables solo para este paso: no se conecta a ninguna
# base ni queda ningún secreto en la imagen.
RUN SECRET_KEY=build-only DATABASE_URL=sqlite:////tmp/build.sqlite3 \
    python manage.py collectstatic --noinput \
    && chmod +x entrypoint.sh

# Se corre sin root: los archivos quedan de root y solo se leen.
RUN useradd --create-home --uid 10001 app
USER app

# Render inyecta PORT (10000 por defecto); 8000 es solo para correrlo local.
EXPOSE 8000

ENTRYPOINT ["./entrypoint.sh"]
