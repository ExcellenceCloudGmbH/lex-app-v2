from mozilla_django_oidc.auth import OIDCAuthenticationBackend


class RefreshingOIDCAuthenticationBackend(OIDCAuthenticationBackend):
    """
    After the code→token exchange, keep the full JSON on the request
    so the callback view can pull out refresh_token + expires_in.
    """

    def get_token(self, request, code, code_verifier=None):
        token_response = super().get_token(request, code, code_verifier)
        # store it for later
        setattr(request, "_oidc_token_response", token_response)
        return token_response
