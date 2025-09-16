import base64
from typing import Optional, Dict
import os
import traceback
import urllib.parse

import streamlit as st

from lex_app.rest_api.views.authentication.KeycloakManager import KeycloakManager


def normalize(d: Dict[str, str]) -> Dict[str, str]:
    return {(k or "").strip().lower(): (v or "").strip() for k, v in (d or {}).items()}


def get_bearer_token(headers: Dict[str, str]) -> Optional[str]:
    for name in ("authorization", "x-forwarded-access-token", "x-auth-request-access-token"):
        val = headers.get(name)
        if not val:
            continue
        return strip_bearer(val)

    return None


def strip_bearer(value: str) -> str:
    v = (value or "").strip()
    if v.lower().startswith("bearer "):
        return v.split(" ", 1)[1].strip()
    return v


def get_user_info(access_token):
    import requests

    # Replace these variables with your Keycloak server details
    keycloak_url = os.getenv("KEYCLOAK_URL")
    realm_name = os.getenv("KEYCLOAK_REALM")

    # Endpoint to get user info
    user_info_url = f"{keycloak_url}/realms/{realm_name}/protocol/openid-connect/userinfo"

    # Set up the headers with the access token
    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    # Make the request
    response = requests.get(user_info_url, headers=headers)

    # Check the response status
    if response.status_code == 200:
        user_info = response.json()
        print("User Info:", user_info)
        return user_info
    else:
        print("Failed to get user info:", response.status_code, response.text)
        raise Exception("Failed to get user info from keycloak")



if __name__ == '__main__':
    from lex_app.settings import repo_name

    try:
        exec(f"import {repo_name}._streamlit_structure as streamlit_structure")
        st.set_page_config(layout="wide")
        if 'user_info' not in st.session_state:
            st.session_state.user_info = None
        if 'permissions' not in st.session_state:
            st.session_state.permissions = None


        headers = normalize(st.context.headers)
        access_token = get_bearer_token(headers)
        user_info = get_user_info(access_token)

        kc_manager = KeycloakManager()
        permissions = kc_manager.get_uma_permissions(access_token)

        st.session_state.permissions = permissions
        st.session_state.user_info = user_info


        params = st.query_params  # new, dict-like API
        model = params.get("model")
        pk = params.get("pk")
        if model and pk:

            from django.apps import apps
            from lex_app.settings import repo_name

            model_class = apps.get_model(repo_name, model)
            model_obj = model_class.objects.filter(pk=pk).first()
            model_obj.streamlit_main()
        else:
            streamlit_structure.main()

        if st.button("Logout (complete)"):
            # Optional user landing page after full logout:
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


