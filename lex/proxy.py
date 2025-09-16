# proxy.py
import os
import time
import secrets
from contextlib import suppress
from inspect import signature

from starlette.applications import Starlette
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse, Response
from starlette.requests import Request
from starlette.routing import Route
from authlib.integrations.starlette_client import OAuth
import httpx
import asyncio
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState
from starlette.routing import WebSocketRoute

try:
    from websockets.asyncio.client import connect as ws_connect  # websockets >= 12
except Exception:
    try:
        from websockets.client import connect as ws_connect      # websockets 10/11
    except Exception:
        from websockets import connect as ws_connect

UPSTREAM = os.environ.get("UPSTREAM", "http://127.0.0.1:9000")
SECRET = os.environ["SESSION_SECRET"]                # 32+ random bytes
BASE_URL = os.environ["BASE_URL"]                    # e.g. http://localhost:8502
CALLBACK_URL = BASE_URL + "/auth/callback"

oauth = OAuth()
oauth.register(
    name="oidc",
    client_id='hazem',
    client_secret='ajZBZn4FgS1HK7KIek82SEgMIq1rVwvq',
    server_metadata_url="https://auth.excellence-cloud.dev/realms/lex/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile", "verify": False},
)

# -------------------------------------------------------------------
# Server-side token store (tiny cookie: only sid + email in session)
# -------------------------------------------------------------------
TOKENS: dict[str, dict] = {}  # sid -> {"access_token","id_token","refresh_token","expires_at","email","last_seen"}

def _now() -> int:
    return int(time.time())

def _compute_expires_at(token: dict) -> int:
    if token.get("expires_at"):
        try:
            return int(token["expires_at"])
        except Exception:
            pass
    if token.get("expires_in"):
        try:
            return _now() + int(token["expires_in"])
        except Exception:
            pass
    return 0

def _trim_token(token: dict) -> dict:
    return {
        "access_token": token.get("access_token"),
        "id_token": token.get("id_token"),
        "refresh_token": token.get("refresh_token"),
        "expires_at": _compute_expires_at(token),
    }

def _put_tokens(sid: str, email: str, token: dict):
    TOKENS[sid] = {**_trim_token(token), "email": email, "last_seen": _now()}

def _get_tokens(sid: str) -> dict | None:
    t = TOKENS.get(sid)
    if t:
        t["last_seen"] = _now()
    return t

def _drop_tokens(sid: str):
    TOKENS.pop(sid, None)

def _gc_tokens(max_idle_seconds: int = 60 * 60 * 8):
    cutoff = _now() - max_idle_seconds
    stale = [k for k, v in TOKENS.items() if v.get("last_seen", 0) < cutoff]
    for k in stale:
        TOKENS.pop(k, None)

# Cache OIDC metadata
_OIDC_META: dict | None = None

async def _get_oidc_endpoints() -> dict:
    global _OIDC_META
    if _OIDC_META is None:
        verify = bool(oauth.oidc.client_kwargs.get("verify", True))
        meta_url = getattr(oauth.oidc, "server_metadata_url", None) or \
                   "https://auth.excellence-cloud.dev/realms/lex/.well-known/openid-configuration"
        async with httpx.AsyncClient(timeout=10.0, verify=verify) as client:
            try:
                r = await client.get(meta_url)
                r.raise_for_status()
                _OIDC_META = r.json()
            except Exception:
                _OIDC_META = {}

        issuer = _OIDC_META.get("issuer")
        if not issuer:
            base = httpx.URL(meta_url)
            issuer = str(base.copy_with(path=base.path.replace("/.well-known/openid-configuration", "")))
            _OIDC_META["issuer"] = issuer

        base = httpx.URL(_OIDC_META["issuer"])
        _OIDC_META.setdefault("token_endpoint",
            str(base.copy_with(path=base.path.rstrip("/") + "/protocol/openid-connect/token")))
        _OIDC_META.setdefault("end_session_endpoint",
            str(base.copy_with(path=base.path.rstrip("/") + "/protocol/openid-connect/logout")))

    return {
        "issuer": _OIDC_META.get("issuer"),
        "token_endpoint": _OIDC_META.get("token_endpoint"),
        "end_session_endpoint": _OIDC_META.get("end_session_endpoint"),
    }

async def _refresh_access_token(sid: str) -> bool:
    t = _get_tokens(sid)
    if not t or not t.get("refresh_token"):
        return False

    endpoints = await _get_oidc_endpoints()
    token_url = endpoints["token_endpoint"]
    verify = bool(oauth.oidc.client_kwargs.get("verify", True))

    data = {
        "grant_type": "refresh_token",
        "refresh_token": t["refresh_token"],
        "client_id": 'hazem',
        "client_secret": 'ajZBZn4FgS1HK7KIek82SEgMIq1rVwvq',
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    async with httpx.AsyncClient(timeout=10.0, verify=verify) as client:
        try:
            resp = await client.post(token_url, data=data, headers=headers)
            if resp.status_code >= 400:
                return False
            new_token = resp.json()
        except Exception:
            return False

    # store rotated tokens
    _put_tokens(sid, t.get("email", ""), new_token)
    return True

async def _ensure_valid_access_token(session: dict) -> dict | None:
    sid = (session.get("user") or {}).get("sid")
    if not sid:
        return None
    t = _get_tokens(sid)
    if not t or not t.get("access_token"):
        return None
    if _now() < int(t.get("expires_at") or 0) - 30:
        return t
    ok = await _refresh_access_token(sid)
    return _get_tokens(sid) if ok else None

# ------------------- Auth routes -------------------

async def login(request: Request):
    return await oauth.oidc.authorize_redirect(request, CALLBACK_URL)

async def auth_callback(request: Request):
    token = await oauth.oidc.authorize_access_token(request)
    userinfo = token.get("userinfo") or await oauth.oidc.userinfo(token=token)

    sid = secrets.token_urlsafe(16)
    email = userinfo.get("email") or ""
    _put_tokens(sid, email, token)

    # tiny client-side session
    request.session["user"] = {"email": email, "sid": sid}

    # opportunistic GC
    _gc_tokens()

    return RedirectResponse(url="/", status_code=303)

# Local-only logout (like oauth2-proxy /oauth2/logout)
async def oauth2_logout(request: Request):
    sid = (request.session.get("user") or {}).get("sid")
    if sid:
        _drop_tokens(sid)
    request.session.clear()
    return RedirectResponse(url="/")

async def oauth2_sign_out(request: Request):
    # Always land here after IdP logout
    rd = BASE_URL

    # grab id_token from our server-side store
    sid = (request.session.get("user") or {}).get("sid")
    t = _get_tokens(sid) if sid else None
    id_token = (t or {}).get("id_token")

    # clear local session & tokens first
    if sid:
        _drop_tokens(sid)
    request.session.clear()

    # if we don't have an id_token, just go straight to BASE_URL
    if not id_token:
        return RedirectResponse(url=rd, status_code=303)

    # build Keycloak RP-initiated logout URL
    endpoints = await _get_oidc_endpoints()
    end_session_endpoint = (
        endpoints.get("end_session_endpoint")
        or (endpoints.get("issuer", "").rstrip("/") + "/protocol/openid-connect/logout")
    )

    qp = httpx.QueryParams({
        "id_token_hint": id_token,
        "post_logout_redirect_uri": rd,
        "client_id": 'hazem',
    })

    # redirect to Keycloak; Keycloak will return the browser to BASE_URL
    logout_url = f"{end_session_endpoint}?{qp}"
    return RedirectResponse(url=logout_url, status_code=302)


# Back-compat
async def logout(request: Request):
    return await oauth2_logout(request)

# ------------------- HTTP proxy -------------------

async def proxy(request: Request):
    if "user" not in request.session:
        return RedirectResponse(url="/auth/login")

    tokens = await _ensure_valid_access_token(request.session)
    if not tokens:
        # stale/invalid session
        request.session.clear()
        return RedirectResponse(url="/auth/login")

    method = request.method
    url = httpx.URL(UPSTREAM + request.url.path)
    if request.url.query:
        url = url.copy_with(query=request.url.query)

    # Drop hop-by-hop headers
    hop_by_hop = {
        "host","connection","keep-alive","proxy-authenticate","proxy-authorization",
        "te","trailers","transfer-encoding","upgrade",
    }
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in hop_by_hop}

    # Identity headers
    user = request.session["user"]
    fwd_headers["X-Forwarded-User"] = user.get("email") or ""
    if tokens.get("access_token"):
        fwd_headers["Authorization"] = f"Bearer {tokens['access_token']}"
        fwd_headers["X-Forwarded-Access-Token"] = tokens["access_token"]
    if tokens.get("id_token"):
        fwd_headers["X-Forwarded-Id-Token"] = tokens["id_token"]

    body = await request.body()
    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(follow_redirects=False, timeout=timeout) as client:
        upstream_resp = await client.request(method, url, content=body, headers=fwd_headers)

    drop = hop_by_hop | {"content-length","content-encoding","transfer-encoding","set-cookie"}
    resp_headers = {k: v for k, v in upstream_resp.headers.items() if k.lower() not in drop}

    response = Response(content=upstream_resp.content,
                        status_code=upstream_resp.status_code,
                        headers=resp_headers)

    get_list = getattr(upstream_resp.headers, "get_list", None)
    if callable(get_list):
        for c in upstream_resp.headers.get_list("set-cookie"):
            response.headers.append("set-cookie", c)
    else:
        for k, v in upstream_resp.headers.items():
            if k.lower() == "set-cookie":
                response.headers.append("set-cookie", v)

    return response

# ------------------- WebSocket proxy -------------------

def _ws_header_kwarg():
    params = signature(ws_connect).parameters
    for name in ("extra_headers","additional_headers","headers"):
        if name in params:
            return name
    return None

WS_HEADER_KWARG = _ws_header_kwarg()
WS_HAS_ORIGIN = "origin" in signature(ws_connect).parameters
WS_HAS_SUBPROTOCOLS = "subprotocols" in signature(ws_connect).parameters

def _upstream_ws_url_and_origin(client_ws_url: str) -> tuple[str, str]:
    base = httpx.URL(UPSTREAM)
    ws_scheme = "wss" if base.scheme == "https" else "ws"
    target = base.copy_with(
        scheme=ws_scheme,
        path=httpx.URL(client_ws_url).path,
        query=httpx.URL(client_ws_url).query,
    )
    origin = f"{base.scheme}://{base.host}"
    if base.port:
        origin += f":{base.port}"
    return str(target), origin

async def ws_proxy(websocket: WebSocket):
    scope_session = websocket.scope.get("session") or {}
    if "user" not in scope_session:
        with suppress(Exception):
            if websocket.client_state != WebSocketState.DISCONNECTED:
                await websocket.close(code=4401)
        return

    tokens = await _ensure_valid_access_token({"user": scope_session.get("user")})
    if not tokens:
        with suppress(Exception):
            if websocket.client_state != WebSocketState.DISCONNECTED:
                await websocket.close(code=4401)
        return

    target_url, upstream_origin = _upstream_ws_url_and_origin(str(websocket.url))

    excluded = {
        "connection","upgrade","sec-websocket-key","sec-websocket-version",
        "sec-websocket-protocol","te","proxy-authorization","proxy-authenticate","keep-alive","host","origin",
    }
    fwd = [(k, v) for k, v in websocket.headers.items() if k.lower() not in excluded]

    user = scope_session["user"]
    fwd.append(("X-Forwarded-User", user.get("email") or ""))
    if tokens.get("access_token"):
        fwd.append(("Authorization", f"Bearer {tokens['access_token']}"))
        fwd.append(("X-Forwarded-Access-Token", tokens["access_token"]))
    if tokens.get("id_token"):
        fwd.append(("X-Forwarded-Id-Token", tokens["id_token"]))

    raw_subprotos = websocket.headers.get("sec-websocket-protocol")
    client_subprotocols = [p.strip() for p in raw_subprotos.split(",")] if raw_subprotos else []

    kwargs = {}
    if WS_HEADER_KWARG:
        if not WS_HAS_ORIGIN:
            fwd.append(("Origin", upstream_origin))
        kwargs[WS_HEADER_KWARG] = fwd
    if WS_HAS_ORIGIN:
        kwargs["origin"] = upstream_origin
    if WS_HAS_SUBPROTOCOLS and client_subprotocols:
        kwargs["subprotocols"] = client_subprotocols
    if "max_size" in signature(ws_connect).parameters:
        kwargs["max_size"] = None

    try:
        async with ws_connect(target_url, **kwargs) as upstream:
            chosen = getattr(upstream, "subprotocol", None)
            if websocket.client_state == WebSocketState.CONNECTING:
                await websocket.accept(subprotocol=chosen)

            async def pump_client_to_upstream():
                try:
                    while True:
                        msg = await websocket.receive()
                        t = msg.get("type")
                        if t == "websocket.disconnect":
                            break
                        if "text" in msg:
                            await upstream.send(msg["text"])
                        elif "bytes" in msg:
                            await upstream.send(msg["bytes"])
                except WebSocketDisconnect:
                    pass

            async def pump_upstream_to_client():
                import websockets
                try:
                    while True:
                        data = await upstream.recv()
                        if isinstance(data, (bytes, bytearray)):
                            await websocket.send_bytes(data)
                        else:
                            await websocket.send_text(data)
                except websockets.exceptions.ConnectionClosed:
                    pass

            t1 = asyncio.create_task(pump_client_to_upstream())
            t2 = asyncio.create_task(pump_upstream_to_client())
            done, pending = await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
                with suppress(asyncio.CancelledError):
                    await t
    finally:
        with suppress(Exception):
            if websocket.client_state != WebSocketState.DISCONNECTED:
                await websocket.close()

# ------------------- Routing -------------------

routes = [
    Route("/auth/login", login),
    Route("/auth/callback", auth_callback),

    # Streamlit-compatible logout endpoints
    Route("/oauth2/logout", oauth2_logout),     # local-only
    Route("/oauth2/sign_out", oauth2_sign_out), # RP-initiated (Keycloak)

    Route("/auth/logout", oauth2_logout),       # back-compat

    WebSocketRoute("/{path:path}", ws_proxy),
    Route("/{path:path}", proxy),  # catch-all
]
app = Starlette(routes=routes)
app.add_middleware(SessionMiddleware, secret_key=SECRET, https_only=False, same_site="lax")
