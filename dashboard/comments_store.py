"""
Outreach comments storage — Supabase (PostgREST) backend.

Append-only outreach log per property address. Reads are open to anyone
holding the anon key (the dashboard), inserts likewise; UPDATE/DELETE have
no RLS policy, so the log is immutable through this API — right for an
audit trail.

Config resolution order (first hit wins):
  1. Streamlit secrets  ->  st.secrets["supabase"]  (Streamlit Cloud)
  2. env vars           ->  SUPABASE_URL / SUPABASE_ANON_KEY
  3. local .env file    ->  SUPABASE_URL=... / SUPABASE_ANON_KEY=...

Table schema (create once in the Supabase SQL editor — see README):
  outreach_comments(id, county, address, author, outreach_type, comment,
                    outreach_date, created_at)
"""
import os

import requests

OUTREACH_TYPES = [
    "called", "visited", "emailed", "mailed",
    "responded", "not_interested", "follow_up_needed", "other",
]


def _load_env_file():
    """Read key=value lines from .env at repo root (no dependency on dotenv)."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, ".env")
    vals = {}
    if os.path.exists(path):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    vals[k.strip()] = v.strip()
    return vals


def _config():
    """Return (base_url, anon_key) or (None, None) if unconfigured."""
    try:  # Streamlit secrets first (cloud deployment)
        import streamlit as st
        if "supabase" in st.secrets:
            sec = st.secrets["supabase"]
            key = sec.get("anon_key") or sec.get("publishable_key")
            return sec["url"].rstrip("/"), key
    except Exception:
        pass

    env = dict(os.environ)
    env.update(_load_env_file())
    url = env.get("SUPABASE_URL", "").rstrip("/")
    # Supabase renamed the anon key to "publishable key" (2025) — accept both
    key = env.get("SUPABASE_ANON_KEY") or env.get("SUPABASE_PUBLISHABLE_KEY") or ""
    return (url, key) if url and key else (None, None)


def _norm(text) -> str:
    """Stable property-key normalization — case/space drift tolerated."""
    return " ".join(str(text or "").upper().split())


class CommentsStore:
    def __init__(self):
        self.url, self.key = _config()
        self.headers = ({
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        } if self.url else {})
        self.enabled = bool(self.url)

    # ------------------------------------------------------------ writes
    def add_comment(self, county: str, address: str, author: str,
                    comment: str, outreach_type: str = "",
                    outreach_date: str = "") -> dict:
        """Insert one outreach note. Returns the created row.

        county/address stored twice: raw for display, *_key normalized
        (upper/whitespace-collapsed) for lookups — dashboard exports change
        letter-casing between runs and threads must not get orphaned.
        """
        resp = requests.post(
            f"{self.url}/rest/v1/outreach_comments",
            headers=self.headers,
            json=[{
                "county": str(county or "").strip(),
                "address": str(address or "").strip(),
                "county_key": _norm(county),
                "address_key": _norm(address),
                "author": str(author or "").strip() or "anonymous",
                "outreach_type": outreach_type,
                "comment": comment.strip(),
                "outreach_date": outreach_date or None,
            }],
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()[0]

    # ------------------------------------------------------------- reads
    def get_comments(self, county: str, address: str) -> list:
        """All notes for one property, oldest first (key-normalized match)."""
        resp = requests.get(
            f"{self.url}/rest/v1/outreach_comments",
            headers=self.headers,
            params={
                "county_key": f"eq.{_norm(county)}",
                "address_key": f"eq.{_norm(address)}",
                "order": "created_at.asc",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()

    def all_commented(self) -> set:
        """Set of normalized (COUNTY, ADDRESS) keys that have notes —
        used to badge map markers without per-marker requests."""
        resp = requests.get(
            f"{self.url}/rest/v1/outreach_comments",
            headers={k: v for k, v in self.headers.items()
                     if k != "Prefer"},
            params={"select": "county_key,address_key", "limit": "50000"},
            timeout=20,
        )
        resp.raise_for_status()
        return {(r["county_key"], r["address_key"]) for r in resp.json()}
