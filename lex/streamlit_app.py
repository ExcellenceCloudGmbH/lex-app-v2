import os
import time
import json
import base64
import uuid
from typing import Optional, Dict, Any

import streamlit as st
import requests
import redis

# =========================
# CONFIG (use ENV in prod)
# =========================
# It's recommended to set these as environment variables in a real environment
KEYCLOAK_URL = os.getenv("KEYCLOAK_URL", "http://exc-testing.com")
REALM_NAME = os.getenv("REALM_NAME", "lex")
CLIENT_ID = os.getenv("CLIENT_ID", "LEX_LOCAL_ENV")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "O1dT6TEXjsQWbRlzVxjwfUnNHPnwDmMF")
REDIRECT_URI = os.getenv(
    "REDIRECT_URI", "http://localhost:8501"
)  # MUST match client config
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Session inactivity timeout in Redis (seconds)
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "86400"))  # 24h

# =========================
# ENDPOINTS
# =========================
AUTH_URL = f"{KEYCLOAK_URL}/realms/{REALM_NAME}/protocol/openid-connect/auth"
TOKEN_URL = f"{KEYCLOAK_URL}/realms/{REALM_NAME}/protocol/openid-connect/token"
LOGOUT_URL = f"{KEYCLOAK_URL}/realms/{REALM_NAME}/protocol/openid-connect/logout"
USERINFO_URL = f"{KEYCLOAK_URL}/realms/{REALM_NAME}/protocol/openid-connect/userinfo"

# =========================
# REDIS CONNECTION
# =========================
try:
    r = redis.Redis.from_url(REDIS_URL, decode_responses=True)
    r.ping()  # Check connection
except redis.exceptions.ConnectionError as e:
    st.error(
        f"Could not connect to Redis. Please ensure it's running at {REDIS_URL}. Error: {e}"
    )
    st.stop()


# =========================
# REDIS HELPERS
# =========================
def _rkey(sid: str, kind: str) -> str:
    return f"kcapp:{sid}:{kind}"


def r_set_json(key: str, value: Dict[str, Any], ttl: int = SESSION_TTL_SECONDS):
    r.set(key, json.dumps(value), ex=ttl)


def r_get_json(key: str) -> Optional[Dict[str, Any]]:
    raw = r.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def r_del_prefix(prefix: str):
    for k in r.scan_iter(match=f"{prefix}*"):
        r.delete(k)


def refresh_ttl(sid: str):
    for kind in ("tokens", "userinfo"):
        key = _rkey(sid, kind)
        if r.exists(key):
            r.expire(key, SESSION_TTL_SECONDS)


# =========================
# SESSION & OIDC HELPERS
# =========================
def get_or_create_sid() -> str:
    """Gets session ID from URL or creates a new one."""
    # Uses st.query_params which is the modern API
    sid = st.query_params.get("sid")
    if not sid:
        sid = uuid.uuid4().hex
        st.query_params["sid"] = sid
        st.rerun()
    return sid


def build_login_url(sid: str) -> str:
    """Constructs the Keycloak login URL."""
    params = {
        "client_id": CLIENT_ID,
        "response_type": "code",
        # Pass the sid in the state parameter to have it on callback
        "state": sid,
        "redirect_uri": REDIRECT_URI,
        "scope": "openid profile email",
    }
    req = requests.Request("GET", AUTH_URL, params=params)
    return req.prepare().url


def decode_jwt(token: str) -> Dict[str, Any]:
    """Decodes a JWT token without validation (for inspecting payload)."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return {"error": "Invalid JWT format"}
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = base64.b64decode(payload_b64).decode("utf-8")
        return json.loads(payload)
    except Exception as e:
        return {"error": f"Decode failed: {e}"}


def is_access_token_expired(tokens: Dict[str, Any]) -> bool:
    """Checks if the access token is expired."""
    if not tokens or "access_token" not in tokens:
        return True
    decoded = decode_jwt(tokens["access_token"])
    exp = decoded.get("exp", 0)
    # Add a small buffer (e.g., 10 seconds) to be safe
    return time.time() > float(exp - 10)


def exchange_code_for_tokens(code: str) -> Optional[Dict[str, Any]]:
    """Exchanges authorization code for tokens."""
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    try:
        # IMPORTANT: In production, verify should be True.
        # Set to False only for local dev with self-signed certs if needed.
        resp = requests.post(TOKEN_URL, data=data, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        st.error(f"Failed to exchange code for tokens: {e}")
        return None


def try_refresh_tokens(refresh_token: str) -> Optional[Dict[str, Any]]:
    """Refreshes tokens using a refresh token."""
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    }
    try:
        # IMPORTANT: In production, verify should be True.
        resp = requests.post(TOKEN_URL, data=data, timeout=20)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        st.error(f"Failed to refresh tokens: {e}")
        return None


def fetch_userinfo(access_token: str) -> Optional[Dict[str, Any]]:
    """Fetches user information from the userinfo endpoint."""
    try:
        # IMPORTANT: In production, verify should be True.
        resp = requests.get(
            USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        st.error(f"Failed to fetch user info: {e}")
        return None


# =========================
# SESSION MANAGEMENT API
# =========================
def load_tokens(sid: str) -> Optional[Dict[str, Any]]:
    refresh_ttl(sid)
    return r_get_json(_rkey(sid, "tokens"))


def save_tokens(sid: str, tokens: Dict[str, Any]):
    r_set_json(_rkey(sid, "tokens"), tokens)


def load_userinfo(sid: str) -> Optional[Dict[str, Any]]:
    refresh_ttl(sid)
    return r_get_json(_rkey(sid, "userinfo"))


def save_userinfo(sid: str, info: Dict[str, Any]):
    r_set_json(_rkey(sid, "userinfo"), info)


def clear_session(sid: str):
    r_del_prefix(f"kcapp:{sid}:")


# =========================
# SAFE REDIRECT UTILITY
# =========================
def redirect(url: str):
    """Issues a safe client-side redirect and stops the script."""
    st.markdown(
        f"""
        <script>
            window.location.href = "{url}";
        </script>
        <p>Redirecting to login... <a href="{url}">Click here if you are not redirected</a>.</p>
        """,
        unsafe_allow_html=True,
    )
    # Stop the script to ensure the redirect happens immediately
    st.stop()


# =========================
# MAIN APP
# =========================
def main():
    st.set_page_config(page_title="Keycloak Auth (Redis)", layout="wide")
    st.title("🔑 Keycloak + Streamlit (Redis)")

    # The 'state' param from Keycloak will contain our SID. The 'sid' param is for our own use.
    # We prioritize 'state' on callback, then 'sid'.
    sid = st.query_params.get("state") or st.query_params.get("sid")
    auth_code = st.query_params.get("code")

    # If SID is missing entirely, create a new one.
    if not sid:
        sid = uuid.uuid4().hex
        st.query_params["sid"] = sid
        st.rerun()

    # == Step 1: Handle the login callback from Keycloak ==
    if auth_code:
        # We have an auth code, so we're in the callback flow.
        # The 'sid' is the 'state' parameter we sent.
        tokens = exchange_code_for_tokens(str(auth_code))
        if tokens:
            save_tokens(sid, tokens)
            ui = fetch_userinfo(tokens.get("access_token", ""))
            if ui:
                save_userinfo(sid, ui)

            # Clean URL by removing 'code' & 'state', keeping only our 'sid'
            st.query_params.clear()
            st.query_params["sid"] = sid
            st.success("Login successful!")
            st.rerun()  # Rerun to show the main app view
        else:
            # Exchange failed, clean URL and show error
            st.query_params.clear()
            st.query_params["sid"] = sid
            st.error("Login failed during token exchange. Please try again.")
            st.rerun()

    # == Step 2: Check for existing session tokens ==
    tokens = load_tokens(sid)

    # == Step 3: If no tokens, show the login page ==
    if not tokens:
        st.subheader("Please log in to continue")
        if st.button("Login with Keycloak", type="primary", use_container_width=True):
            login_url = build_login_url(sid)
            redirect(login_url)
        st.info(
            "Your session is stored in Redis and will persist across browser tabs and reloads."
        )
        return

    # == Step 4: If tokens exist, check for expiration and refresh if needed ==
    if is_access_token_expired(tokens):
        st.warning("Access token expired. Attempting to refresh...")
        rt = tokens.get("refresh_token")
        if rt:
            new_tokens = try_refresh_tokens(rt)
            if new_tokens:
                save_tokens(sid, new_tokens)
                tokens = new_tokens  # Update tokens for the current run
                st.success("Tokens refreshed successfully.")
                # Short sleep to let the user see the message, then rerun
                time.sleep(1)
                st.rerun()
            else:
                # Refresh failed, treat as logout
                clear_session(sid)
                st.error(
                    "Session expired. Your refresh token may be invalid. Please log in again."
                )
                st.rerun()
        else:
            # No refresh token, force logout
            clear_session(sid)
            st.error(
                "Session expired and no refresh token is available. Please log in again."
            )
            st.rerun()

    # == Step 5: User is logged in and tokens are valid, show the app ==
    user_info = load_userinfo(sid)
    if not user_info and tokens.get("access_token"):
        user_info = fetch_userinfo(tokens["access_token"])
        if user_info:
            save_userinfo(sid, user_info)

    display_name = (user_info or {}).get("preferred_username", "User")
    st.header(f"Welcome, {display_name}! 👋")

    # Logout button
    if st.button("Logout", use_container_width=True):
        try:
            params = {
                "client_id": CLIENT_ID,
                "post_logout_redirect_uri": REDIRECT_URI,
                "id_token_hint": tokens.get("id_token"),
            }
            # This request logs the user out of Keycloak itself
            requests.get(LOGOUT_URL, params=params, timeout=10)
        except Exception as e:
            # Don't block logout if Keycloak is unreachable
            print(f"Keycloak logout request failed: {e}")

        # Clear local session data and rotate SID
        clear_session(sid)
        st.query_params.clear()
        st.success("You have been logged out.")
        time.sleep(1)
        st.rerun()

    st.divider()
    st.subheader("Authenticated User Information")
    st.json(user_info or {"error": "User info unavailable."})

    with st.expander("Show Session Tokens"):
        st.text_area("Access Token", tokens.get("access_token", ""), height=120)
        st.text_area("ID Token", tokens.get("id_token", ""), height=120)
        st.text_area("Refresh Token", tokens.get("refresh_token", ""), height=120)
        st.subheader("Decoded Access Token Payload")
        st.json(decode_jwt(tokens.get("access_token", "")))

    # Bump TTL on each interaction
    refresh_ttl(sid)


if __name__ == "__main__":
    main()
