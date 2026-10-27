from rest_framework import permissions, viewsets, pagination, status
from rest_framework.decorators import action
from rest_framework.generics import get_object_or_404
from rest_framework.response import Response

from telerehabilitation_API.authentication.permissions import IsAdminOrTherapist
from telerehabilitation_API.therapy.models import SkeletonPoint
from telerehabilitation_API.therapy.serializers import SkeletonPointSerializer



class SkeletonViewSet(viewsets.ModelViewSet):
    queryset = SkeletonPoint.objects.all()
    serializer_class = SkeletonPointSerializer
    # M1 (docs/DECISIONES_TECNICAS.md): los vecinos por defecto definen las ternas de todos
    # los ejercicios; antes cualquier usuario con token podía cambiarlos.
    permission_classes = [permissions.IsAuthenticated, IsAdminOrTherapist]
    pagination_class = None

    def create(self, request, *args, **kwargs):
        """
        Handles creation of a single object or a list of objects.
        """
        is_many = isinstance(request.data, list)
        if not is_many:
            return super().create(request, *args, **kwargs)

        serializer = self.get_serializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    @action(detail=False, methods=['patch'])
    def bulk_update(self, request, *args, **kwargs):
        """
        Handles partial update of a list of objects.
        """
        if not isinstance(request.data, list):
            return Response({"detail": "Expected a list of items for bulk update."}, status=status.HTTP_400_BAD_REQUEST)

        updated_objects = []
        for item in request.data:
            obj_id = item.get('id')
            if obj_id is None:
                continue
            obj = get_object_or_404(self.get_queryset(), pk=obj_id)
            serializer = self.get_serializer(obj, data=item, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            updated_objects.append(serializer.data)

        return Response(updated_objects, status=status.HTTP_200_OK)
