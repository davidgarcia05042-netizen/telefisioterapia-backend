from django.contrib.auth.models import Permission
from rest_framework import permissions


class CanCreateTherapy(permissions.BasePermission):

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True

        return request.user.has_perm('therapy.add_therapy')


class CanEditTherapy(permissions.BasePermission):

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True

        return request.user.has_perm('therapy.change_therapy')


class CanDeleteTherapy(permissions.BasePermission):

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        user_permissions = request.user.get_all_permissions()
        return 'therapy.delete_therapy' in user_permissions
