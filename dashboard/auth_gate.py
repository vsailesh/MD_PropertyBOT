"""Shared editor gate — used by the dashboard app and the review page."""
import os

import streamlit as st


def editor_emails():
    """Allowlist from st.secrets (list) or env/.env (comma-separated)."""
    try:
        vals = list(st.secrets.get("EDITOR_EMAILS", []))
    except Exception:
        vals = []
    if not vals:
        from comments_store import _load_env_file
        env = dict(os.environ)
        env.update(_load_env_file())
        raw = env.get("EDITOR_EMAILS", "")
        vals = [v.strip() for v in raw.split(",") if v.strip()]
    return {v.lower() for v in vals}


def current_user():
    """Logged-in Google identity or None. st.user is always present in
    recent Streamlit; it's just empty when unauthenticated."""
    try:
        u = st.user
        email = (getattr(u, "email", None) or (u.get("email") if hasattr(u, "get") else None) or "")
        if email:
            name = getattr(u, "name", None) or email
            return {"email": str(email).lower(), "name": str(name)}
    except Exception:
        pass
    return None


def is_editor(user):
    return bool(user and user["email"] in editor_emails())
