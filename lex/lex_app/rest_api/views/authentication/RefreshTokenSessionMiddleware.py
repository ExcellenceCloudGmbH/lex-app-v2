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

    def process_request(self, request):
        if not self.is_refreshable_url(request):
            return

        now = time.time()
        exp = request.session.get("oidc_access_token_expiration", 0)
        if exp > now:
            return

        rt = request.session.get("oidc_refresh_token")
        if not rt:
            return super().process_request(request)

        # build a cache-safe key that isn’t None
        sid = request.session.session_key or request.COOKIES.get("sessionid")
        lock_key = f"oidc_refresh_lock_{sid}"
        if not cache.add(lock_key, "1", timeout=30):
            LOGGER.debug("Another process is already refreshing the token")
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

            # 1) update access_token + expiration
            request.session["oidc_access_token"] = tok["access_token"]
            expires_in = tok.get(
                "expires_in",
                import_from_settings("OIDC_RENEW_ID_TOKEN_EXPIRY_SECONDS", 900),
            )
            request.session["oidc_access_token_expiration"] = now + expires_in

            # 2) rotate refresh_token
            if "refresh_token" in tok:
                request.session["oidc_refresh_token"] = tok["refresh_token"]

            # 3) store the new id_token + expiration
            if "id_token" in tok:
                request.session["oidc_id_token"] = tok["id_token"]
                claims = jwt.get_unverified_claims(tok["id_token"])
                exp_claim = claims.get("exp")
                if not exp_claim:
                    raise ValueError("no exp claim in returned id_token")
                request.session["oidc_id_token_expiration"] = exp_claim

            request.session.save()
            LOGGER.debug("successfully refreshed via refresh_token grant")

            if getattr(request, "user", None) and request.user.is_authenticated:
                # use the newly refreshed access token
                access_token = tok["access_token"]
                sync_user_permissions(request.user, access_token)
                LOGGER.debug("synced UMA permissions after refresh")
            return

        except Exception as e:
            LOGGER.warning(
                "refresh_token grant failed, falling back: %s", e, exc_info=True
            )

        finally:
            cache.delete(lock_key)

        # fallback to prompt=none
        return super().process_request(request)
