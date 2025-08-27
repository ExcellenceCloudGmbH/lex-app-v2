from django.db import models
from lex_app.rest_api.views.authentication.KeycloakManager import KeycloakManager
from django_lifecycle import LifecycleModel, hook, AFTER_UPDATE, AFTER_CREATE

from lex.lex_app.rest_api.context import context_id


class LexModel(LifecycleModel):

    created_by = models.TextField(null=True, blank=True, editable=False)
    edited_by = models.TextField(null=True, blank=True, editable=False)

    class Meta:
        abstract = True

    @hook(AFTER_UPDATE)
    def update_edited_by(self):
        context = context_id.get()
        if context and hasattr(context["request_obj"], "auth"):
            self.edited_by = f"{context['request_obj'].auth['name']} ({context['request_obj'].auth['sub']})"
        else:
            self.edited_by = "Initial Data Upload"

    @hook(AFTER_CREATE)
    def update_created_by(self):
        context = context_id.get()
        if context and hasattr(context["request_obj"], "auth"):
            self.created_by = f"{context['request_obj'].auth['name']} ({context['request_obj'].auth['sub']})"
        else:
            self.created_by = "Initial Data Upload"

    def _get_user_permissions(self):
        """Helper method to get permissions for the current user and this model."""
        context = context_id.get()
        if not context or not hasattr(context.get("request_obj"), "session"):
            return set()

        access_token = context["request_obj"].session.get("oidc_access_token")
        if not access_token:
            return set()

        # Cache results on the instance to avoid multiple API calls per request
        if not hasattr(self, "_cached_permissions"):
            kc_manager = KeycloakManager()
            resource_name = f"{self._meta.app_label}.{self._meta.model_name}"

            # Check for record-specific permissions first, then fall back to model-level
            record_perms = kc_manager.get_permissions(
                access_token, resource_name, str(self.pk)
            )
            model_perms = kc_manager.get_permissions(access_token, resource_name)
            self._cached_permissions = record_perms.union(model_perms)

        return self._cached_permissions

    def can_create(self):
        return "create" in self._get_user_permissions()

    def can_export(self):
        return "export" in self._get_user_permissions()

    def can_edit(self):
        return "edit" in self._get_user_permissions()

    def can_delete(self):
        return "delete" in self._get_user_permissions()

    def can_show(self):
        """Checks if the user can either view a single record ('show') or a list of them ('list')."""
        user_perms = self._get_user_permissions()
        return "show" in user_perms

    def can_list(self, user_perms):
        """Checks if the user can either view a single record ('show') or a list of them ('list')."""
        return "list" in user_perms["scopes"]

    def can_view_field(self, field_name):
        """Checks for field-level view permissions."""
        # For this to work, you'd create resources in Keycloak like:
        # 'lex_app.mymodel.myfield' with a 'view' scope.
        context = context_id.get()
        if not context or not hasattr(context.get("request_obj"), "session"):
            return False

        access_token = context["request_obj"].session.get("oidc_access_token")
        if not access_token:
            return False

        kc_manager = KeycloakManager()
        resource_name = f"{self._meta.app_label}.{self._meta.model_name}.{field_name}"
        field_permissions = kc_manager.get_permissions(access_token, resource_name)

        return "view" in field_permissions
