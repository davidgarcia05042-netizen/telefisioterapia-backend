from rest_framework import permissions


class CanCreateExercise(permissions.BasePermission):

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True

        return request.user.has_perm('therapy.add_exercise')


class CanEditExercise(permissions.BasePermission):

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True

        return request.user.has_perm('therapy.change_exercise')


class CanDeleteExercise(permissions.BasePermission):

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True

        return request.user.has_perm('therapy.delete_exercise')
