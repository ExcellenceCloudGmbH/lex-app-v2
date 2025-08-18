# myapp/auth_backend.py
from mozilla_django_oidc.auth import OIDCAuthenticationBackend


class KeycloakUMAAuthBackend(OIDCAuthenticationBackend):
    def filter_users_by_claims(self, claims):
        # standard lookup by sub or preferred_username
        return super().filter_users_by_claims(claims)

    def create_user(self, claims):
        user = super().create_user(claims)
        self._sync_permissions(user)
        return user

    def update_user(self, user, claims):
        user = super().update_user(user, claims)
        self._sync_permissions(user)
        return user

    def _sync_permissions(self, user, access_token=None):

        if not access_token:
            access_token = self.request.session.get("oidc_access_token")
        if not access_token:
            return

        # sync_user_permissions(user, access_token)
