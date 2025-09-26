import time, logging, requests
from django.core.cache import cache
from mozilla_django_oidc.utils import import_from_settings
from mozilla_django_oidc.middleware import SessionRefresh
from jose import jwt
from lex.lex_app.rest_api.views.authentication.helpers import sync_user_permissions

LOGGER = logging.getLogger(__name__)


class RefreshTokenSessionMiddleware(SessionRefresh):
    def __init__(self, get_response):
        super().__init__(get_response)
        self.token_endpoint = import_from_settings("OIDC_OP_TOKEN_ENDPOINT")
        self.client_id = import_from_settings("OIDC_RP_CLIENT_ID")
        self.client_secret = import_from_settings("OIDC_RP_CLIENT_SECRET")
        self.extra_params = import_from_settings("OIDC_REFRESH_TOKEN_EXTRA_PARAMS", {})
        # allow toggling cert-verify in settings
        self.verify_ssl = import_from_settings("OIDC_VERIFY_SSL", False)

    def should_refresh_token(self, request):
        """Skip refresh for static files, health checks, etc."""
        skip_paths = ['/static/', '/media/', '/health/', '/metrics/']
        return not any(request.path.startswith(path) for path in skip_paths)

    def process_request(self, request):
        if not self.is_refreshable_url(request) or not self.should_refresh_token(request):
            return

        # last_refresh = request.session.get("last_token_refresh", 0)
        # min_interval = 5  # seconds
        #
        # if time.time() - last_refresh < min_interval:
        #     return
        #
        # request.session["last_token_refresh"] = time.time()

        rt = request.session.get("oidc_refresh_token")
        if not rt:
            return super().process_request(request)

        # Remove expiration check - refresh on every request
        sid = request.session.session_key or request.COOKIES.get("sessionid")
        lock_key = f"oidc_refresh_lock_{sid}"
        if not cache.add(lock_key, "1", timeout=30):
            LOGGER.debug("Another process is refreshing the token")
            return

        try:
            data = {
                "grant_type": "refresh_token",
                "refresh_token": rt,
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
            data.update(self.extra_params)

            r = requests.post(self.token_endpoint, data=data, verify=self.verify_ssl)
            r.raise_for_status()
            tok = r.json()

            now = time.time()

            # Update tokens
            request.session["oidc_access_token"] = tok["access_token"]
            expires_in = tok.get("expires_in", 900)
            request.session["oidc_access_token_expiration"] = now + expires_in

            if "refresh_token" in tok:
                request.session["oidc_refresh_token"] = tok["refresh_token"]

            if "id_token" in tok:
                request.session["oidc_id_token"] = tok["id_token"]
                claims = jwt.get_unverified_claims(tok["id_token"])
                exp_claim = claims.get("exp")
                if not exp_claim:
                    raise ValueError("no exp claim in returned id_token")
                request.session["oidc_id_token_expiration"] = exp_claim

            request.session.save()
            LOGGER.debug("Token refreshed on every request")
            print(request['access_token'])

            if getattr(request, "user", None) and request.user.is_authenticated:
                access_token = tok["access_token"]
                sync_user_permissions(request.user, access_token)
                LOGGER.debug("UMA permissions synced after refresh")

        except Exception as e:
            LOGGER.warning("Token refresh failed: %s", e, exc_info=True)
        finally:
            cache.delete(lock_key)