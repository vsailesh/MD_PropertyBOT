"""Google-gate AppTest: allowlist logic both ways against the mock user.

Streamlit's AppTest runs in bare mode — st.user is a fixed mock
(test@example.com) and no real OAuth happens. The repo's
.streamlit/secrets.toml (loaded because tests run from the repo root)
supplies EDITOR_EMAILS via st.secrets, which takes priority over the env
fallback — so the mock is NOT an editor here. That's exactly what these
tests pin down:

  - signed-in mock, email not in allowlist -> read-only + warning banner
  - _editor_emails() unit: reads st.secrets, lowercased set, real editors in
"""
import os
import runpy
import sys

import pytest
from streamlit.testing.v1 import AppTest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.path.join(REPO, "dashboard", "app.py")


def _run():
    at = AppTest.from_file(DASH, default_timeout=120)
    at.run()
    return at


def _sidebar_text(at):
    return " ".join(s.value for s in at.sidebar.markdown)


def test_mock_user_not_allowlisted():
    at = _run()
    assert not at.exception, f"app raised: {at.exception}"
    warns = " ".join(w.value for w in at.sidebar.warning)
    succs = " ".join(s.value for s in at.sidebar.success)
    assert "not on the editor allowlist" in warns
    assert "Commenting as" not in succs
    # outreach section renders in read-only shape (no row selected → caption)
    assert any("Select a row" in c.value for c in at.caption)


def test_editor_emails_from_secrets():
    # import the helpers without running the whole script body twice
    ns = runpy.run_path(DASH)  # executes app.py once (bare mode: st.* no-ops)
    editors = ns["_editor_emails"]()
    assert isinstance(editors, set)
    assert all(e == e.lower() for e in editors)
    assert "adhvaithinc@gmail.com" in editors  # project owner account
    assert len(editors) >= 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
