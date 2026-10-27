from rest_framework import permissions


class CanCreateRoutine(permissions.BasePermission):

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True

        return request.user.has_perm('therapy.add_routine')


class CanEditRoutine(permissions.BasePermission):

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True

        return request.user.has_perm('therapy.change_routine')


class CanDeleteRoutine(permissions.BasePermission):

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True

        return request.user.has_perm('therapy.delete_routine')
