"""
Firma HMAC compartida con el Servicio de Procesamiento (docs/DECISIONES_TECNICAS.md, 4.1).

Mismo algoritmo que app/utils/hmac_auth.py del servicio, sin cambios:

    X-Timestamp: <epoch en segundos>
    X-Signature: hex(HMAC_SHA256(secreto, "<timestamp>." + cuerpo_crudo))

La plataforma firma lo que envía (POST /video/) y verifica los callbacks. El
timestamp entra en la firma y se rechaza fuera de una ventana de 300 s, para
que una firma capturada no se pueda reutilizar (anti-replay).

El POST /video/ se firma leyendo el cuerpo desde un archivo por bloques
(compute_file_signature): con el video entero en memoria, cada envío dejaba
retenido en el worker unos MB del tamaño del video y Render cortó el backend
por memoria (M5, 2026-10-19). La firma es la misma que la de compute_signature
sobre esos bytes.
"""
import hashlib
import hmac
import time

MAX_SKEW_SECONDS = 300


def compute_signature(timestamp, body, secret):
    """Firma hex de `body` (bytes) con `timestamp` (texto) y `secret`."""
    message = timestamp.encode('utf-8') + b'.' + body
    return hmac.new(secret.encode('utf-8'), message, hashlib.sha256).hexdigest()


def compute_file_signature(timestamp, path, secret, chunk_bytes=1024 * 1024):
    """Igual que compute_signature(timestamp, <contenido de path>, secret), leyendo por bloques."""
    mac = hmac.new(secret.encode('utf-8'), timestamp.encode('utf-8') + b'.', hashlib.sha256)
    with open(path, 'rb') as body:
        for chunk in iter(lambda: body.read(chunk_bytes), b''):
            mac.update(chunk)
    return mac.hexdigest()


def signed_file_headers(path, secret, now=None):
    """Encabezados X-Timestamp y X-Signature para enviar el contenido de `path` firmado."""
    timestamp = str(int(time.time() if now is None else now))
    return {'X-Timestamp': timestamp, 'X-Signature': compute_file_signature(timestamp, path, secret)}


def signed_headers(body, secret, now=None):
    """Encabezados X-Timestamp y X-Signature para enviar `body` firmado."""
    timestamp = str(int(time.time() if now is None else now))
    return {'X-Timestamp': timestamp, 'X-Signature': compute_signature(timestamp, body, secret)}


def is_valid_signature(timestamp, signature, body, secret, now=None):
    """
    Verifica firma y frescura del timestamp.

    Sin secreto configurado se rechaza todo: aceptar callbacks sin verificar
    dejaría que cualquiera escribiera ángulos clínicos.

    Se compara en bytes con `hmac.compare_digest` (tiempo constante): con `==`
    el tiempo de respuesta filtra cuántos caracteres coinciden. En bytes y no en
    texto porque compare_digest lanza TypeError con texto no ASCII, y un
    encabezado raro nunca debe producir un 500.
    """
    if not secret or not signature or not timestamp:
        return False
    try:
        sent_at = int(timestamp)
    except (TypeError, ValueError):
        return False

    current = time.time() if now is None else now
    if abs(current - sent_at) > MAX_SKEW_SECONDS:
        return False

    expected = compute_signature(timestamp, body, secret).encode('ascii')
    return hmac.compare_digest(expected, signature.encode('utf-8', errors='replace'))
