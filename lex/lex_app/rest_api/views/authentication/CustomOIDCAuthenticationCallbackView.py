import time
from mozilla_django_oidc.views import OIDCAuthenticationCallbackView


class CustomOIDCAuthenticationCallbackView(OIDCAuthenticationCallbackView):
    """
    On login_success, pull the raw JSON off the request and
    write refresh_token + access_token expiry into the session.
    """

    def login_success(self):
        resp = super().login_success()
        tok = getattr(self.request, "_oidc_token_response", {})

        # save the refresh_token if the OP gave us one
        if "refresh_token" in tok:
            self.request.session["oidc_refresh_token"] = tok["refresh_token"]

        # save access_token + expiration
        if "access_token" in tok:
            self.request.session["oidc_access_token"] = tok["access_token"]
        if "expires_in" in tok:
            self.request.session["oidc_access_token_expiration"] = (
                time.time() + tok["expires_in"]
            )

        return resp
