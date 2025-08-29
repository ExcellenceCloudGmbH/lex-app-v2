from django.db import models
from django_lifecycle import LifecycleModel, hook, AFTER_UPDATE, AFTER_CREATE


class LexModel(LifecycleModel):
    """
    An abstract base model that provides a flexible, override-driven permission system.

    Key Architectural Changes:
    - **`can_read` Returns Fields**: The `can_read` method is the source of truth
      for field-level security, returning a set of visible field names.
    - **`can_export` Returns Fields**: This method mirrors the `can_read` logic
      for data exports, returning a set of fields the user is allowed to export.
    - **Override Pattern**: All `can_*` methods are designed to be
      overridden in subclasses for custom business logic, with a fallback to
      Keycloak permissions.
    """

    created_by = models.TextField(null=True, blank=True, editable=False)
    edited_by = models.TextField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True

    @hook(AFTER_UPDATE)
    def update_edited_by(self):
        self.edited_by = "User (Update Hook Needs Refactor)"

    @hook(AFTER_CREATE)
    def update_created_by(self):
        self.created_by = "User (Create Hook Needs Refactor)"

    def _get_keycloak_permissions(self, request):
        """
        Private helper to get the cached UMA permissions for this model/instance
        from the request object.
        """
        if not request or not hasattr(request, 'user_permissions'):
            return set()

        resource_name = f"{self._meta.app_label}.{self.__class__.__name__}"
        all_perms = request.user_permissions

        model_scopes = set()
        record_scopes = set()

        for perm in all_perms:
            if perm.get("rsname") == resource_name:
                if self.pk and str(self.pk) == perm.get("resource_set_id"):
                    record_scopes.update(perm.get("scopes", []))
                elif perm.get("resource_set_id") is None:
                    model_scopes.update(perm.get("scopes", []))

        return record_scopes if record_scopes else model_scopes

    # --- Field-Level Permission Methods ---

    def can_read(self, request):
        """
        Determines which fields of this instance are visible to the current user.
        Consumed by the serializer to control API output.

        Returns: A set of visible field names.
        """
        record_scopes = self._get_keycloak_permissions(request)
        if "read" in record_scopes:
            return {f.name for f in self._meta.fields}
        return set()

    def can_export(self, request):
        """
        Determines which fields of this instance are exportable for the current user.
        Should be called by your data export logic.

        Returns: A set of exportable field names.
        """
        record_scopes = self._get_keycloak_permissions(request)
        if "export" in record_scopes:
            return {f.name for f in self._meta.fields}
        return set()

    # --- Action-Based Permission Methods ---

    def can_create(self, request):
        """Checks for the 'create' scope in Keycloak."""
        return "create" in self._get_keycloak_permissions(request)

    def can_edit(self, request):
        record_scopes = self._get_keycloak_permissions(request)
        if "edit" in record_scopes:
            return {f.name for f in self._meta.fields}
        return set()


    def can_delete(self, request):
        """Checks for the 'delete' scope in Keycloak."""
        return "delete" in self._get_keycloak_permissions(request)

    def can_list(self, request):
        """Checks for the 'list' scope in Keycloak."""
        return "list" in self._get_keycloak_permissions(request)

