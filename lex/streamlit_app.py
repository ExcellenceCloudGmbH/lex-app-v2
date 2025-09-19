import base64
import json
from typing import Optional, Dict
import os
import traceback
import urllib.parse
import jwt
import requests
from datetime import datetime, timezone
import logging

import streamlit as st

from lex_app.rest_api.views.authentication.KeycloakManager import KeycloakManager
from django.conf import settings

logger = logging.getLogger(__name__)


def normalize(d: Dict[str, str]) -> Dict[str, str]:
    """Normalize dictionary keys and values to lowercase."""
    return {(k or "").strip().lower(): (v or "").strip() for k, v in (d or {}).items()}


def get_bearer_token(headers: Dict[str, str]) -> Optional[str]:
    """Extract bearer token from various header formats."""
    for name in ("authorization", "x-forwarded-access-token", "x-auth-request-access-token"):
        val = headers.get(name)
        if not val:
            continue
        return strip_bearer(val)
    return None


def strip_bearer(value: str) -> str:
    """Remove 'Bearer ' prefix from token."""
    v = (value or "").strip()
    if v.lower().startswith("bearer "):
        return v.split(" ", 1)[1].strip()
    return v


def get_user_info(access_token):
    """Get user info from Keycloak using access token."""
    keycloak_url = os.getenv("KEYCLOAK_URL")
    realm_name = os.getenv("KEYCLOAK_REALM")

    if not keycloak_url or not realm_name:
        logger.error("KEYCLOAK_URL and KEYCLOAK_REALM must be set")
        return None

    userinfo_url = f"{keycloak_url}/realms/{realm_name}/protocol/openid-connect/userinfo"

    try:
        headers = {"Authorization": f"Bearer {access_token}"}
        response = requests.get(userinfo_url, headers=headers, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Failed to get user info: {e}")
        return None


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# -------------------------
# Session state initialization
# -------------------------
def init_session_state() -> None:
    # Ensure all keys exist; never assume presence across reruns
    if "authenticated" not in st.session_state:
        st.session_state.authenticated = False
    if "auth_method" not in st.session_state:
        st.session_state.auth_method = ""
    if "user_id" not in st.session_state:
        st.session_state.user_id = ""
    if "user_email" not in st.session_state:
        st.session_state.user_email = ""
    if "user_username" not in st.session_state:
        st.session_state.user_username = ""
    if "permissions" not in st.session_state:
        st.session_state.permissions = {}
    if "user_info" not in st.session_state:
        st.session_state.user_info = {"sub": "", "email": "", "preferred_username": ""}


# -------------------------
# Header utilities
# -------------------------
def normalize_headers(h: Dict[str, str]) -> Dict[str, str]:
    # Case-insensitive access
    return {(k or "").strip().lower(): (v or "").strip() for k, v in (h or {}).items()}


def bearer_from_headers(h: Dict[str, str]) -> Optional[str]:
    # Prefer Authorization, fallback to X-Forwarded-Access-Token, X-Auth-Request-Access-Token
    for name in ("authorization", "x-forwarded-access-token", "x-auth-request-access-token"):
        v = h.get(name)
        if not v:
            continue
        v = v.strip()
        if v.lower().startswith("bearer "):
            return v.split(" ", 1)[1].strip()
        return v
    return None


def decode_jwt_claims_no_verify(token: str) -> Dict:
    # Proxy already validated upstream; here we only need claims to hydrate identity
    try:
        return jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
    except Exception as e:
        logger.warning(f"JWT decode (no verify) failed: {e}")
        return {}


# -------------------------
# Authentication
# -------------------------
def authenticate_from_proxy_or_jwt() -> None:
    # If already authenticated in this session, do not re-evaluate
    if st.session_state.authenticated:
        return

    headers = getattr(st.context, "headers", {}) or {}
    h = normalize_headers(headers)

    # Try Streamlit identity headers from proxy
    user_id = (
        h.get("x-streamlit-user-id")
        or headers.get("X-Streamlit-User-ID", "")
        or headers.get("X-Streamlit-User-Id", "")
        or ""
    )
    user_email = (
        h.get("x-streamlit-user-email")
        or headers.get("X-Streamlit-User-Email", "")
        or ""
    )
    user_username = (
        h.get("x-streamlit-user-username")
        or headers.get("X-Streamlit-User-Username", "")
        or ""
    )
    auth_method = (
        h.get("x-streamlit-auth-method")
        or headers.get("X-Streamlit-Auth-Method", "")
        or ""
    )
    perms_raw = (
        h.get("x-streamlit-user-permissions")
        or headers.get("X-Streamlit-User-Permissions", "")
        or ""
    )

    # If user_id empty, attempt to derive from JWT claims present in headers
    # Works for iframe flow (JWT) and for session flow when proxy added Authorization bearer
    if not user_id:
        token = bearer_from_headers(h)
        if token:
            claims = decode_jwt_claims_no_verify(token)
            user_id = claims.get("sub") or user_id
            user_email = claims.get("email") or user_email
            user_username = claims.get("preferred_username") or user_username
            if not auth_method:
                auth_method = "jwt"

    # Fallback: if still no user_id but email exists, use email as stable identifier
    if not user_id and user_email:
        user_id = user_email

    # Parse permissions JSON safely
    permissions = {}
    if perms_raw:
        try:
            permissions = json.loads(perms_raw)
        except Exception:
            permissions = {}

    # Hydrate session state
    if user_id:
        st.session_state.authenticated = True
        st.session_state.auth_method = auth_method or ("session" if not (bearer_from_headers(h)) else "jwt")
        st.session_state.user_id = user_id
        st.session_state.user_email = user_email
        st.session_state.user_username = user_username or (user_email.split("@")[0] if user_email else "")
        st.session_state.permissions = permissions
        st.session_state.user_info = {
            "sub": st.session_state.user_id,
            "email": st.session_state.user_email,
            "preferred_username": st.session_state.user_username,
        }
        logger.info(
            f"Authenticated via {st.session_state.auth_method} as "
            f"{st.session_state.user_email or st.session_state.user_id}"
        )
    else:
        # Not authenticated: leave session_state.authenticated as False
        pass


# -------------------------
# App bootstrap
# -------------------------
init_session_state()
authenticate_from_proxy_or_jwt()

# Fail closed if not authenticated; Streamlit reruns on interactions so session_state persists per session
if not st.session_state.authenticated:
    st.error("❌ Authentication Error: Missing user information.")
    st.info("Please access this application through the main portal.")
    st.stop()

# -------------------------
# Example UI using hydrated identity
# # -------------------------
# st.title("Streamlit App")
# st.write(f"User ID: {st.session_state.user_id}")
# st.write(f"User Email: {st.session_state.user_email}")
# st.write(f"Username: {st.session_state.user_username}")
# st.write(f"Auth Method: {st.session_state.auth_method}")
#
# with st.expander("Permissions"):
#     st.json(st.session_state.permissions)
#
# with st.expander("User Info"):
#     st.json(st.session_state.user_info)

if __name__ == '__main__':
    from lex_app.settings import repo_name

    try:
        exec(f"import {repo_name}._streamlit_structure as streamlit_structure")

        # Your existing model rendering logic...
        params = st.query_params
        model = params.get("model")
        pk = params.get("pk")

        if model and pk:
            # Instance-level visualization
            try:
                from django.apps import apps
                from lex_app.settings import repo_name

                model_class = apps.get_model(repo_name, model)
                model_obj = model_class.objects.filter(pk=pk).first()

                if model_obj is None:
                    st.error(f"❌ Object with ID {pk} not found")
                elif not hasattr(model_obj, 'streamlit_main'):
                    st.error(f"❌ This model doesn't support visualization")
                else:
                    # Pass user info from session state
                    user = st.session_state.get('user_info')
                    model_obj.streamlit_main(user)

            except LookupError:
                st.error(f"❌ Model '{model}' not found")
            except Exception as e:
                st.error(f"❌ Error: {str(e)}")

        elif model and not pk:
            # Class-level visualization
            try:
                from django.apps import apps
                from lex_app.settings import repo_name

                model_class = apps.get_model(repo_name, model)

                if not hasattr(model_class, 'streamlit_class_main'):
                    st.error(f"❌ This model doesn't support class-level visualization")
                else:
                    # Pass user info and permissions from session state
                    user = st.session_state.get('user_info')
                    permissions = st.session_state.get('permissions')
                    model_class.streamlit_class_main()

            except LookupError:
                st.error(f"❌ Model '{model}' not found")
            except Exception as e:
                st.error(f"❌ Error: {str(e)}")

        else:
            # Default application structure
            streamlit_structure.main()

        # Logout functionality (adjust based on auth method)
        if st.button("Logout (complete)"):
            auth_method = st.session_state.get('auth_method', 'session')

            if auth_method == 'jwt':
                # For JWT auth, just clear session and show message
                st.session_state.clear()
                st.success("✅ Logged out successfully. You can close this window.")
                st.stop()
            else:
                # For session auth, redirect to logout
                rd = urllib.parse.quote("http://localhost:8501", safe="")
                st.markdown(
                    f"<meta http-equiv='refresh' content='0;url=/oauth2/sign_out?rd={rd}'>",
                    unsafe_allow_html=True
                )

    except Exception as e:
        if os.getenv("DEPLOYMENT_ENVIRONMENT") != "PROD":
            raise e
        else:
            with st.expander(":red[An error occurred while trying to load the app.]"):
                st.error(traceback.format_exc())

# import base64
# from typing import Optional, Dict
# import os
# import traceback
# import urllib.parse
# import logging
#
# import streamlit as st
#
# logger = logging.getLogger(__name__)
#
#
# def normalize(d: Dict[str, str]) -> Dict[str, str]:
#     """Normalize headers dictionary with error handling"""
#     try:
#         return {(k or "").strip().lower(): (v or "").strip() for k, v in (d or {}).items()}
#     except Exception as e:
#         logger.error(f"Header normalization failed: {e}")
#         return {}
#
#
# def get_bearer_token(headers: Dict[str, str]) -> Optional[str]:
#     """Extract bearer token from headers with error handling"""
#     try:
#         for name in ("authorization", "x-forwarded-access-token", "x-auth-request-access-token"):
#             val = headers.get(name)
#             if not val:
#                 continue
#             return strip_bearer(val)
#         return None
#     except Exception as e:
#         logger.error(f"Bearer token extraction failed: {e}")
#         return None
#
#
# def strip_bearer(value: str) -> str:
#     """Strip bearer prefix from token with error handling"""
#     try:
#         v = (value or "").strip()
#         if v.lower().startswith("bearer "):
#             return v.split(" ", 1)[1].strip()
#         return v
#     except Exception as e:
#         logger.error(f"Bearer token stripping failed: {e}")
#         return value or ""
#
#
# def get_user_info(access_token):
#     """Get user info from Keycloak with comprehensive error handling"""
#     if not access_token:
#         raise Exception("No access token provided")
#
#     try:
#         import requests
#
#         # Replace these variables with your Keycloak server details
#         keycloak_url = os.getenv("KEYCLOAK_URL")
#         realm_name = os.getenv("KEYCLOAK_REALM")
#
#         if not keycloak_url or not realm_name:
#             raise Exception("Missing Keycloak configuration: KEYCLOAK_URL or KEYCLOAK_REALM")
#
#         # Endpoint to get user info
#         user_info_url = f"{keycloak_url}/realms/{realm_name}/protocol/openid-connect/userinfo"
#
#         # Set up the headers with the access token
#         headers = {
#             "Authorization": f"Bearer {access_token}"
#         }
#
#         # Make the request
#         response = requests.get(user_info_url, headers=headers, timeout=30)
#
#         # Check the response status
#         if response.status_code == 200:
#             user_info = response.json()
#             logger.debug("User Info retrieved successfully")
#             return user_info
#         else:
#             logger.error(f"Keycloak user info request failed: {response.status_code} - {response.text}")
#             raise Exception(f"Failed to get user info from keycloak: HTTP {response.status_code}")
#
#     except requests.exceptions.Timeout:
#         raise Exception("Keycloak request timed out")
#     except requests.exceptions.ConnectionError:
#         raise Exception("Cannot connect to Keycloak server")
#     except requests.exceptions.RequestException as e:
#         raise Exception(f"Keycloak request failed: {str(e)}")
#     except Exception as e:
#         logger.error(f"User info retrieval failed: {e}")
#         raise
#
#
# def safe_import_streamlit_structure():
#     """Safely import streamlit structure with error handling"""
#     try:
#         from lex_app.settings import repo_name
#         exec(f"import {repo_name}._streamlit_structure as streamlit_structure")
#         return streamlit_structure
#     except ImportError as e:
#         logger.error(f"Failed to import streamlit structure: {e}")
#         st.error("❌ Application structure not found")
#         st.stop()
#     except Exception as e:
#         logger.error(f"Unexpected error importing streamlit structure: {e}")
#         st.error("❌ Application configuration error")
#         st.stop()
#
#
# def safe_get_headers():
#     """Safely extract headers with error handling"""
#     try:
#         if not hasattr(st, 'context') or not hasattr(st.context, 'headers'):
#             raise Exception("Streamlit context or headers not available")
#         return normalize(st.context.headers)
#     except Exception as e:
#         logger.error(f"Header extraction failed: {e}")
#         st.error("❌ Unable to access request headers")
#         st.stop()
#
#
# def safe_get_keycloak_manager():
#     """Safely initialize Keycloak manager with error handling"""
#     try:
#         from lex_app.rest_api.views.authentication.KeycloakManager import KeycloakManager
#         return KeycloakManager()
#     except ImportError as e:
#         logger.error(f"Failed to import KeycloakManager: {e}")
#         st.error("❌ Authentication system not available")
#         st.stop()
#     except Exception as e:
#         logger.error(f"KeycloakManager initialization failed: {e}")
#         st.error("❌ Authentication system error")
#         st.stop()
#
#
# def safe_validate_query_params():
#     """Validate query parameters with error handling"""
#     try:
#         params = st.query_params
#         model = params.get("model", "").strip()
#         pk = params.get("pk", "").strip()
#
#         if not model or not pk:
#             return None, None
#
#         # Validate model name format
#         if not model.replace('_', '').replace('-', '').isalnum():
#             st.error(f"❌ Invalid model name format: {model}")
#             st.stop()
#
#         # Validate primary key
#         try:
#             pk_int = int(pk)
#             if pk_int <= 0:
#                 raise ValueError("Primary key must be positive")
#         except ValueError:
#             st.error(f"❌ Invalid primary key format: {pk}")
#             st.stop()
#
#         return model, pk_int
#
#     except Exception as e:
#         logger.error(f"Query parameter validation failed: {e}")
#         st.error("❌ Invalid request parameters")
#         st.stop()
#
#
# def safe_resolve_model(model_name):
#     """Safely resolve Django model with error handling"""
#     try:
#         from django.apps import apps
#         from lex_app.settings import repo_name
#
#         model_class = apps.get_model(repo_name, model_name)
#         return model_class
#
#     except LookupError:
#         logger.error(f"Model '{model_name}' not found in application")
#         st.error(f"❌ Model '{model_name}' not found")
#         st.stop()
#     except Exception as e:
#         logger.error(f"Model resolution failed: {e}")
#         st.error("❌ Database configuration error")
#         st.stop()
#
#
# def safe_get_model_instance(model_class, pk):
#     """Safely retrieve model instance with error handling"""
#     try:
#         model_obj = model_class.objects.filter(pk=pk).first()
#         if not model_obj:
#             model_name = getattr(model_class._meta, 'verbose_name', model_class.__name__)
#             st.error(f"❌ {model_name} with ID {pk} not found")
#             st.stop()
#         return model_obj
#
#     except Exception as e:
#         logger.error(f"Model instance retrieval failed: {e}")
#         st.error("❌ Database query failed")
#         st.stop()
#
#
# def safe_execute_streamlit_method(model_obj):
#     """Safely execute streamlit_main method with error handling"""
#     try:
#         if not hasattr(model_obj, 'streamlit_main'):
#             model_name = getattr(model_obj._meta, 'verbose_name', model_obj.__class__.__name__)
#             st.error(f"❌ {model_name} does not support visualization")
#             st.stop()
#
#         # Execute the streamlit method
#         model_obj.streamlit_main()
#
#     except AttributeError as e:
#         logger.error(f"Streamlit method execution failed: {e}")
#         st.error("❌ Visualization method not available")
#         st.stop()
#     except Exception as e:
#         logger.error(f"Visualization rendering failed: {e}")
#         st.error("❌ Visualization rendering failed")
#         if os.getenv("DEPLOYMENT_ENVIRONMENT") != "PROD":
#             st.exception(e)
#         st.stop()
#
#
# if __name__ == '__main__':
#     try:
#         # Import streamlit structure
#         streamlit_structure = safe_import_streamlit_structure()
#
#         # Configure Streamlit
#         st.set_page_config(layout="wide")
#
#         # Initialize session state
#         if 'user_info' not in st.session_state:
#             st.session_state.user_info = None
#         if 'permissions' not in st.session_state:
#             st.session_state.permissions = None
#
#         # Extract and validate headers
#         headers = safe_get_headers()
#         access_token = get_bearer_token(headers)
#
#         if not access_token:
#             st.error("❌ No authentication token found")
#             st.stop()
#
#         # Get user information
#         try:
#             user_info = get_user_info(access_token)
#             st.session_state.user_info = user_info
#         except Exception as e:
#             logger.error(f"User info retrieval failed: {e}")
#             st.error("❌ Authentication failed")
#             st.stop()
#
#         # Get user permissions
#         try:
#             kc_manager = safe_get_keycloak_manager()
#             permissions = kc_manager.get_uma_permissions(access_token)
#             st.session_state.permissions = permissions
#         except Exception as e:
#             logger.error(f"Permission retrieval failed: {e}")
#             st.error("❌ Permission check failed")
#             st.stop()
#
#         # Check for model visualization request
#         model, pk = safe_validate_query_params()
#
#         if model and pk:
#             # Handle model visualization
#             model_class = safe_resolve_model(model)
#             model_obj = safe_get_model_instance(model_class, pk)
#             safe_execute_streamlit_method(model_obj)
#         else:
#             # Show default structure
#             try:
#                 streamlit_structure.main()
#             except Exception as e:
#                 logger.error(f"Streamlit structure main failed: {e}")
#                 st.error("❌ Application structure error")
#                 if os.getenv("DEPLOYMENT_ENVIRONMENT") != "PROD":
#                     st.exception(e)
#
#         # Logout functionality
#         if st.button("Logout (complete)"):
#             try:
#                 # Optional user landing page after full logout:
#                 rd = urllib.parse.quote("http://localhost:8501", safe="")
#                 st.markdown(
#                     f"<meta http-equiv='refresh' content='0;url=/oauth2/sign_out?rd={rd}'>",
#                     unsafe_allow_html=True
#                 )
#             except Exception as e:
#                 logger.error(f"Logout redirect failed: {e}")
#                 st.error("❌ Logout failed")
#
#     except Exception as e:
#         logger.exception(f"Critical application error: {e}")
#
#         if os.getenv("DEPLOYMENT_ENVIRONMENT") != "PROD":
#             # Development mode - show full error
#             raise e
#         else:
#             # Production mode - show user-friendly error
#             with st.expander(":red[An error occurred while trying to load the app.]"):
#                 st.error("A system error has occurred. Please contact your administrator.")
#                 st.error(f"Error ID: {id(e)}")
#                 # Only show traceback in non-production
#                 if st.checkbox("Show technical details"):
#                     st.code(traceback.format_exc())
