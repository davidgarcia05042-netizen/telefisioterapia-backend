"""
Copia a Cloudflare R2 los videos que siguen en Supabase y reescribe sus URLs
en la base (Exercise.video y ExerciseResult.video). Se corre en el shell de
Render, con las variables R2_* ya cargadas:

    python manage.py migrate_videos_to_r2 --dry-run   # cuántos videos y cuántos MB, sin escribir
    python manage.py migrate_videos_to_r2

Por cada video: lo descarga de la URL pública de Supabase en streaming y lo
sube a R2 con la MISMA clave (exercises/<uuid><ext>, results/<uuid><ext>),
verifica que el tamaño en R2 sea el de origen y solo entonces cambia la URL.
La API tiene 512 MB: el video nunca se carga entero, viaja por bloques de
8 MB.

Idempotente: si el objeto ya está en R2 con el mismo tamaño no se vuelve a
copiar (solo se reescribe la URL), y las filas ya migradas no apuntan a
Supabase, así que una segunda corrida no hace nada. No borra nada de Supabase.

Se saltan las filas "en procesamiento": el aviso del servicio trae la huella
de la URL con la que se envió el video (processing/results.py) y, si la URL
cambiara antes de que llegue, se rechazaría como un video reemplazado. Basta
con volver a correr el comando cuando terminen.
"""
import os
from urllib.parse import urlsplit

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from rest_framework import serializers

from telerehabilitation_API.storage import r2, supabase
from telerehabilitation_API.storage.backend import validate_public_video_url
from telerehabilitation_API.therapy.models import Exercise, ExerciseResult

MODELS = (Exercise, ExerciseResult)
CHUNK_BYTES = 8 * 1024 * 1024
# (conexión, lectura entre bloques): el total depende del tamaño del video.
TIMEOUT = (10, 60)
CONTENT_TYPE_BY_EXTENSION = {ext: content_type for content_type, ext in supabase.ALLOWED_VIDEO_TYPES.items()}


class CopyError(Exception):
    """No se pudo copiar un video; se informa y se sigue con el siguiente."""


class CountingReader:
    """Envuelve la respuesta en streaming y cuenta los bytes que pasan por ella."""

    def __init__(self, raw):
        self.raw = raw
        self.bytes_read = 0

    def read(self, size=-1):
        data = self.raw.read(size)
        self.bytes_read += len(data)
        return data


class Command(BaseCommand):
    help = ('Copia a Cloudflare R2 los videos de ejercicios y resultados que siguen en Supabase '
            '(misma clave) y reescribe sus URLs. No borra nada de Supabase.')

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Solo cuenta los videos y sus MB, sin copiar ni escribir.')

    def handle(self, *args, **options):
        if not settings.SUPABASE_URL:
            raise CommandError('Falta SUPABASE_URL: no se sabe qué URLs son de Supabase.')
        dry_run = options['dry_run']
        missing = r2.missing_settings()
        if missing and not dry_run:
            raise CommandError(f'R2 no está configurado: falta {", ".join(missing)}.')

        prefix = supabase.public_url_prefix()
        rows = [row for model in MODELS
                for row in model.objects.filter(video__startswith=prefix).order_by('pk')]
        if not rows:
            self.stdout.write('Ningún video apunta a Supabase: no hay nada que migrar.')
            return

        if dry_run:
            self.dry_run(rows, prefix)
        else:
            self.migrate(rows, prefix)

    def dry_run(self, rows, prefix):
        total_bytes, unknown, processing = 0, 0, 0
        for row in rows:
            label = self.label(row)
            if row.status == row.PROCESSING:
                processing += 1
                self.stdout.write(f'  {label}: en procesamiento, se saltaría')
                continue
            try:
                validate_public_video_url(row.video)
                size = self.source_size(row.video)
            except (CopyError, serializers.ValidationError) as exc:
                size = None
                self.stdout.write(f'  {label}: {exc}')
            if size is None:
                unknown += 1
            else:
                total_bytes += size
                self.stdout.write(f'  {label}: {size / 1e6:.1f} MB')
        pending = len(rows) - processing
        self.stdout.write(
            f'{pending} video(s) por copiar a R2, {total_bytes / 1e6:.1f} MB'
            + (f' (más {unknown} de tamaño desconocido)' if unknown else '')
            + (f'; {processing} en procesamiento se saltarían' if processing else '')
            + '. Simulación: no se copió ni se escribió nada.'
        )

    def migrate(self, rows, prefix):
        s3 = r2.client()
        # Clave -> tamaño de lo ya copiado en esta corrida: una misma URL puede
        # estar en varias filas y se copia una sola vez.
        copied = {}
        counts = {'copied': 0, 'present': 0, 'rewritten': 0, 'processing': 0, 'errors': 0}
        for row in rows:
            label = self.label(row)
            if row.status == row.PROCESSING:
                counts['processing'] += 1
                self.stdout.write(f'  {label}: en procesamiento, se salta (correr de nuevo al terminar)')
                continue
            old_url = row.video
            key = old_url[len(prefix):]
            try:
                validate_public_video_url(old_url)
                if key not in copied:
                    copied[key], was_present = self.copy(s3, old_url, key)
                    counts['present' if was_present else 'copied'] += 1
            except (CopyError, serializers.ValidationError) as exc:
                counts['errors'] += 1
                self.stderr.write(f'  {label}: {exc}')
                continue

            new_url = r2.public_url(key)
            # Condicionado a que la fila siga igual: si alguien subió otro
            # video o empezó a procesarse mientras se copiaba, no se pisa.
            updated = (type(row).objects.filter(pk=row.pk, video=old_url)
                       .exclude(status=row.PROCESSING).update(video=new_url))
            if updated:
                counts['rewritten'] += 1
                self.stdout.write(f'  {label}: {copied[key] / 1e6:.1f} MB -> {new_url}')
            else:
                self.stdout.write(f'  {label}: cambió durante la copia, se deja como está')

        self.stdout.write(
            f'Copiados: {counts["copied"]}; ya estaban en R2: {counts["present"]}; '
            f'URLs reescritas: {counts["rewritten"]}; en procesamiento: {counts["processing"]}; '
            f'con error: {counts["errors"]}. Nada se borró de Supabase.'
        )
        if counts['errors']:
            raise CommandError(f'{counts["errors"]} video(s) no se migraron; corre el comando de nuevo.')

    def copy(self, s3, url, key):
        """
        Copia `url` a R2 con la clave `key` y devuelve (tamaño, ya_estaba).
        Solo devuelve si el tamaño en R2 coincide con el de origen.
        """
        source_size = self.source_size(url)
        if source_size is not None and self.r2_size(s3, key) == source_size:
            return source_size, True

        from boto3.s3.transfer import TransferConfig
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            response = requests.get(url, stream=True, timeout=TIMEOUT, allow_redirects=False,
                                    headers={'Accept-Encoding': 'identity'})
        except requests.RequestException as exc:
            raise CopyError(f'no se pudo descargar de Supabase ({exc.__class__.__name__})') from exc
        with response:
            if response.status_code != 200:
                raise CopyError(f'Supabase respondió HTTP {response.status_code} al descargar')
            response.raw.decode_content = True
            reader = CountingReader(response.raw)
            # Un hilo y bloques de 8 MB: en memoria hay a lo sumo un bloque.
            config = TransferConfig(multipart_threshold=CHUNK_BYTES, multipart_chunksize=CHUNK_BYTES,
                                    max_concurrency=1, use_threads=False)
            try:
                s3.upload_fileobj(reader, settings.R2_BUCKET, key,
                                  ExtraArgs={'ContentType': self.content_type(response, key)}, Config=config)
            except (BotoCoreError, ClientError) as exc:
                raise CopyError(f'R2 rechazó la subida ({exc.__class__.__name__})') from exc

        expected = source_size if source_size is not None else reader.bytes_read
        uploaded = self.r2_size(s3, key)
        if reader.bytes_read != expected or uploaded != expected:
            raise CopyError(f'tamaño distinto (origen {expected}, leídos {reader.bytes_read}, '
                            f'en R2 {uploaded}); la URL no se cambió')
        return expected, False

    def source_size(self, url):
        """Content-Length del objeto en Supabase, o None si no lo informa."""
        try:
            response = requests.head(url, timeout=TIMEOUT, allow_redirects=False,
                                     headers={'Accept-Encoding': 'identity'})
        except requests.RequestException as exc:
            raise CopyError(f'Supabase no respondió ({exc.__class__.__name__})') from exc
        if response.status_code != 200:
            raise CopyError(f'Supabase respondió HTTP {response.status_code}')
        declared = response.headers.get('Content-Length', '')
        return int(declared) if declared.isdigit() else None

    def r2_size(self, s3, key):
        """Tamaño del objeto en R2, o None si no existe."""
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return s3.head_object(Bucket=settings.R2_BUCKET, Key=key)['ContentLength']
        except ClientError as exc:
            if exc.response.get('Error', {}).get('Code') in ('404', 'NoSuchKey', 'NotFound'):
                return None
            raise CopyError(f'R2 respondió {exc.response.get("Error", {}).get("Code")} al consultar') from exc
        except BotoCoreError as exc:
            raise CopyError(f'R2 no respondió ({exc.__class__.__name__})') from exc

    @staticmethod
    def content_type(response, key):
        """El de Supabase si es de video; si no, el que corresponde a la extensión."""
        declared = response.headers.get('Content-Type', '').split(';')[0].strip()
        if declared in supabase.ALLOWED_VIDEO_TYPES:
            return declared
        extension = os.path.splitext(urlsplit(key).path)[1].lower()
        return CONTENT_TYPE_BY_EXTENSION.get(extension, 'application/octet-stream')

    @staticmethod
    def label(row):
        return f'{type(row).__name__} {row.pk}'
