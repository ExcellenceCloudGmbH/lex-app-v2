import streamlit as st

st.set_page_config(layout="wide")
st.title("Streamlit with Keycloak Authentication")

# 1) If they’re not already logged in, immediately kick off the OIDC flow
if not st.user.is_logged_in:
    st.login("keycloak")  # ← this redirects to Keycloak (and back)
    st.stop()  # ← nothing below this line runs until after login

# 2) From here on out you know they’re authenticated
st.write(f"Hello, {st.user.name}!")
st.write(st.user)
st.write(st.session_state)

if st.button("Log out"):
    st.logout()
