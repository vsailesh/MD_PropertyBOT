"""Shared editor/admin gate — used by the dashboard app and its pages.

Editors = EDITOR_EMAILS in st.secrets (or env/.env) UNION the DB-managed
`editors` table (admin can add/remove at runtime). Admins = ADMIN_EMAILS
in st.secrets — they also get the Admin page.
"""
import os

import streamlit as st


def _secret_emails(key):
    try:
        vals = list(st.secrets.get(key, []))
    except Exception:
        vals = []
    if not vals:
        from comments_store import _load_env_file
        env = dict(os.environ)
        env.update(_load_env_file())
        raw = env.get(key, "")
        vals = [v.strip() for v in raw.split(",") if v.strip()]
    return {v.lower() for v in vals}


def editor_emails():
    """Secrets allowlist only (static)."""
    return _secret_emails("EDITOR_EMAILS")


def _db_editor_emails():
    """DB-managed allowlist, cached 60s — missing table degrades to empty."""
    from comments_store import CommentsStore
    store = CommentsStore()
    if not store.enabled:
        return set()
    try:
        return {str(r.get("email", "")).lower() for r in store.get_editors()}
    except Exception:
        return set()


@st.cache_data(ttl=60, show_spinner=False)
def _all_editor_emails_cached():
    return editor_emails() | _db_editor_emails()


def all_editor_emails():
    """Full editor allowlist: secrets + DB (cached)."""
    try:
        return _all_editor_emails_cached()
    except Exception:
        return editor_emails()


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
    return bool(user and user["email"] in all_editor_emails())


def admin_emails():
    return _secret_emails("ADMIN_EMAILS")


def is_admin(user):
    return bool(user and user["email"] in admin_emails())
