# myapp/permissions.py
from rest_framework import permissions

ACTION_MAP = {
    "GET": ("list", "show"),
    "POST": ("create",),
    "PUT": ("edit",),
    "PATCH": ("edit",),
    "DELETE": ("delete",),
}


class KeycloakUMAPermission(permissions.BasePermission):
    def has_permission(self, request, view):
        user_perms = request.user.profile.uma_permissions or []
        resource = f"{view.queryset.model._meta.verbose_name_plural}"
        actions = ACTION_MAP.get(request.method, ())
        # allow if any of the UMA perms covers one of these scopes
        return any(
            p["rsname"] == resource and scope in p["scopes"]
            for p in user_perms
            for scope in actions
        )

    def has_object_permission(self, request, view, obj):
        # record-level: require the permission’s resource_set_id to match obj.id
        user_perms = request.user.profile.uma_permissions or []
        actions = ACTION_MAP.get(request.method, ())
        rsid = getattr(obj, "id", None)
        return any(
            p["rsname"] == resource
            and scope in p["scopes"]
            and (
                p.get("resource_set_id") == str(rsid)
                or p.get("resource_set_id") is None
            )
            for p in user_perms
            for scope in actions
        )
