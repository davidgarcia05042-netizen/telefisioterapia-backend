"""
Almacenamiento de videos activo, según STORAGE_BACKEND ("supabase" o "r2"), y
validación de las URLs de video que recibe la API.

Los dos módulos tienen la misma interfaz (missing_settings, build_object_path,
create_signed_upload_url, public_url, StorageError): la vista de subida no
sabe cuál está activo.
"""
from urllib.parse import urlsplit

from django.conf import settings
from rest_framework import serializers

from telerehabilitation_API.storage import r2, supabase

BACKENDS = {'supabase': supabase, 'r2': r2}


def active():
    """Módulo donde se suben los videos nuevos."""
    return BACKENDS[settings.STORAGE_BACKEND]


def public_url_prefixes():
    """
    Prefijos públicos aceptados: el del bucket de Supabase y el de R2, cada uno
    si está configurado. Durante la transición conviven los dos (los videos ya
    guardados siguen en Supabase hasta que migrate_videos_to_r2 los copie),
    sin importar cuál esté activo para las subidas nuevas.

    Los dos terminan en "/": así pub-xxxx.r2.dev.evil.com o
    pub-xxxx.r2.dev@evil.com no pasan por empezar igual que el host propio.
    """
    prefixes = []
    if settings.SUPABASE_URL:
        prefixes.append(supabase.public_url_prefix())
    if settings.R2_PUBLIC_BASE_URL:
        prefixes.append(r2.public_url_prefix())
    return prefixes


def object_key(value):
    """Clave del objeto si `value` es una URL pública propia; '' si no."""
    for prefix in public_url_prefixes():
        if value.startswith(prefix):
            return value[len(prefix):]
    return ''


def validate_public_video_url(value):
    """
    Validador anti-SSRF para los campos `video` (docs/DECISIONES_TECNICAS.md, 3.2).

    El backend luego DESCARGA esa URL para mandarla al servicio: sin esta
    validación, cualquiera con un token podría hacer que el servidor pidiera
    URLs internas o de terceros. Solo se aceptan objetos del bucket propio de
    Supabase o de R2.

    Además del prefijo se rechazan "..", "%" y "\\" (que podrían salir del
    bucket tras normalizar la ruta), query y fragmento: las rutas que genera
    build_object_path nunca los llevan.
    """
    if not public_url_prefixes():
        raise serializers.ValidationError(
            'El almacenamiento de videos no está configurado (falta SUPABASE_URL o R2_PUBLIC_BASE_URL).'
        )
    key = object_key(value)
    parts = urlsplit(value)
    if (
        not key
        or parts.query
        or parts.fragment
        or '..' in key.split('/')
        or '%' in key
        or '\\' in value
    ):
        raise serializers.ValidationError(
            'La URL del video debe ser un objeto del bucket de videos de la plataforma (Supabase o R2).'
        )
