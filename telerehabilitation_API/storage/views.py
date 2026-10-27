import logging

from drf_yasg.utils import swagger_auto_schema
from rest_framework import serializers, status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from telerehabilitation_API.authentication.permissions import IsAdminTherapistOrPatient, is_staff_member
from telerehabilitation_API.storage import backend, supabase

logger = logging.getLogger(__name__)


class UploadUrlRequestSerializer(serializers.Serializer):
    kind = serializers.ChoiceField(choices=list(supabase.UPLOAD_FOLDERS))
    content_type = serializers.ChoiceField(choices=list(supabase.ALLOWED_VIDEO_TYPES))
    # Solo se usa su extensión; el nombre nunca llega al almacenamiento.
    filename = serializers.CharField(required=False, allow_blank=True, max_length=255, default='')


class UploadUrlView(APIView):
    """
    POST /storage/upload_url: URL firmada para que el navegador (o la app)
    suba un video. El paciente solo puede pedirla para sus resultados
    (`kind=result`), no para los videos de referencia (M1, docs/DECISIONES_TECNICAS.md).

    La URL es de Supabase Storage o de Cloudflare R2 según STORAGE_BACKEND; la
    respuesta tiene la misma forma con los dos.
    """

    permission_classes = [IsAuthenticated, IsAdminTherapistOrPatient]

    @swagger_auto_schema(
        operation_summary='URL firmada de subida de un video (Supabase Storage o R2)',
        operation_description=(
            'Devuelve upload_url (PUT directo desde el navegador, con el mismo '
            'Content-Type enviado aquí), public_url (la que luego se envía a '
            '/exercise_results o /exercises/{id}/video/) y path.'
        ),
        request_body=UploadUrlRequestSerializer,
    )
    def post(self, request):
        # 503 y no un fallo al arrancar: el backend debe servir el resto de la
        # API aunque el almacenamiento aún no esté configurado.
        storage = backend.active()
        missing = storage.missing_settings()
        if missing:
            return Response(
                {'error': f'Almacenamiento de videos no configurado: falta {", ".join(missing)}.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        payload = UploadUrlRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        data = payload.validated_data
        if data['kind'] != 'result' and not is_staff_member(request.user):
            return Response({'detail': 'No tienes permiso para realizar esta acción.'}, status=status.HTTP_403_FORBIDDEN)

        path = storage.build_object_path(data['kind'], data['content_type'], data['filename'])
        try:
            upload_url = storage.create_signed_upload_url(path, data['content_type'])
        except storage.StorageError:
            logger.exception('No se pudo firmar la URL de subida para %s', path)
            return Response(
                {'error': 'El almacenamiento de videos no emitió la URL de subida; intenta de nuevo.'},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(
            {'upload_url': upload_url, 'public_url': storage.public_url(path), 'path': path},
            status=status.HTTP_200_OK,
        )
