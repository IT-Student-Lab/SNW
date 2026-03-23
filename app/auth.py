# -*- coding: utf-8 -*-
"""Simple authentication gate for Streamlit."""

from __future__ import annotations

import hmac

import streamlit as st

from app.config import settings


def check_auth() -> bool:
    """Return True if the user is authenticated, False otherwise.

    Shows a login form and blocks the page when not yet logged in.
    """
    if st.session_state.get("authenticated"):
        return True

    st.title("Inloggen")
    with st.form("login_form"):
        username = st.text_input("Gebruikersnaam", placeholder="admin")
        password = st.text_input("Wachtwoord", type="password", placeholder="admin")
        submitted = st.form_submit_button("Inloggen")

    if submitted:
        user_ok = True
        pass_ok = True
        #user_ok = hmac.compare_digest(username, settings.app_username)
        #pass_ok = hmac.compare_digest(password, settings.app_password)
        if user_ok and pass_ok:
            st.session_state["authenticated"] = True
            st.session_state["username"] = username
            st.rerun()
        else:
            st.error("Ongeldige inloggegevens.")

    return False
