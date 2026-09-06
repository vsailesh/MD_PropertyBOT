"""Admin — manage editors, review login activity, moderate notes.

Admins only (ADMIN_EMAILS in st.secrets). Everyone else gets a prompt.
Tabs: 👥 Editors · 📜 Logins · 🗑️ Notes
"""
import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from comments_store import CommentsStore
from auth_gate import current_user, is_admin, editor_emails

st.set_page_config(page_title="Admin", layout="wide", page_icon="🛡️")
st.title("🛡️ Admin")
st.caption("Manage editors, review login activity, moderate notes.")

user = current_user()
if not (user and is_admin(user)):
    st.info("Admins only. Ask the owner to add your email to ADMIN_EMAILS.")
    st.stop()

store = CommentsStore()
if not store.enabled:
    st.error("Comments backend not configured. Set SUPABASE_URL / SUPABASE_ANON_KEY (see README).")
    st.stop()

tab_users, tab_logins, tab_notes = st.tabs(["👥 Editors", "📜 Logins", "🗑️ Notes"])

# ------------------------------------------------------------ editors
with tab_users:
    st.subheader("Editor allowlist")
    st.caption(
        "Two sources combined: static `EDITOR_EMAILS` in secrets (change = "
        "edit file + restart) and the DB list below (instant). New DB "
        "editors must ALSO be test users in Google Cloud Console → Auth "
        "Platform → Audience, or Google blocks their login before the app "
        "sees them."
    )

    c1, c2 = st.columns([3, 2])
    with c1:
        st.markdown("**DB-managed editors**")
        try:
            db_rows = store.get_editors()
        except Exception as e:
            db_rows = []
            st.warning(f"Editors table not reachable: {e} — paste supabase_admin.sql first.")
        if db_rows:
            edf = pd.DataFrame(db_rows)
            st.dataframe(
                edf[["email", "name", "added_by", "created_at"]],
                hide_index=True, use_container_width=True,
            )
            del_email = st.selectbox("Remove editor", [""] + sorted(edf["email"].tolist()))
            if del_email and st.button(f"➖ Remove {del_email}"):
                try:
                    store.remove_editor(del_email)
                    st.cache_data.clear()
                    st.success(f"Removed {del_email}.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Remove failed: {e}")
        else:
            st.caption("No DB editors yet — add one on the right.")

    with c2:
        st.markdown("**Add editor**")
        n_email = st.text_input("Gmail address", key="new_editor_email")
        n_name = st.text_input("Display name (optional)", key="new_editor_name")
        if st.button("➕ Add editor"):
            if "@" not in n_email:
                st.warning("Enter a valid email.")
            else:
                try:
                    store.add_editor(n_email, n_name or n_email, user["email"])
                    st.cache_data.clear()
                    st.success(f"Added {n_email}. Remember: also add as GCP test user.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Add failed: {e}")

        st.markdown("---")
        st.markdown("**Static editors (secrets.toml)**")
        st.code("\n".join(sorted(editor_emails())) or "(none)")

# ------------------------------------------------------------ logins
with tab_logins:
    st.subheader("Editor login activity")
    st.caption("One row per editor session per page — recorded when a signed-in editor opens the dashboard or review page.")
    try:
        events = store.get_auth_events()
    except Exception as e:
        events = []
        st.warning(f"Auth events not reachable: {e} — paste supabase_admin.sql first.")
    if events:
        edf = pd.DataFrame(events)
        # tz-aware timestamptz -> naive UTC (same reason as review page)
        edf["created_at"] = pd.to_datetime(edf["created_at"], errors="coerce", utc=True).dt.tz_localize(None)
        emails = sorted(edf["email"].dropna().unique().tolist())
        sel = st.multiselect("Filter by email", emails, default=emails)
        fdf = edf[edf["email"].isin(sel)].sort_values("created_at", ascending=False)

        m1, m2, m3 = st.columns(3)
        m1.metric("Events", f"{len(fdf):,}")
        m2.metric("Distinct editors", f"{fdf['email'].nunique():,}")
        m3.metric("Last 7 days", f"{len(fdf[fdf['created_at'] >= pd.Timestamp.now() - pd.Timedelta(days=7)]):,}")

        st.dataframe(
            fdf[["created_at", "email", "name", "page"]],
            column_config={"created_at": st.column_config.DatetimeColumn("When", format="YYYY-MM-DD HH:mm")},
            hide_index=True, use_container_width=True, height=420,
        )
        st.download_button(
            "📥 Export login log (CSV)",
            data=fdf[["created_at", "email", "name", "page"]].to_csv(index=False).encode("utf-8"),
            file_name="editor_logins.csv", mime="text/csv",
        )
    else:
        st.caption("No login events yet.")

# ------------------------------------------------------------ notes
with tab_notes:
    st.subheader("Moderate notes")
    st.caption("Deleting is permanent (row leaves Supabase). Readers lose the note immediately.")
    try:
        rows = store.all_comments()
    except Exception as e:
        rows = []
        st.error(f"Could not load notes: {e}")
    if rows:
        ndf = pd.DataFrame(rows)
        ndf["created_at"] = pd.to_datetime(ndf["created_at"], errors="coerce", utc=True).dt.tz_localize(None)
        authors = sorted(ndf["author"].dropna().unique().tolist())
        sel_authors = st.multiselect("Author", authors, default=authors)
        fdf = ndf[ndf["author"].isin(sel_authors)].sort_values("created_at", ascending=False)

        st.dataframe(
            fdf[["id", "created_at", "author", "outreach_type", "county", "address", "comment"]],
            column_config={"created_at": st.column_config.DatetimeColumn("Logged", format="YYYY-MM-DD HH:mm")},
            hide_index=True, use_container_width=True, height=420,
            on_select="rerun", selection_mode="single-row",
            key="admin_notes_table",
        )
        sel_rows = st.session_state.get("admin_notes_table", {}).get("selection", {}).get("rows", [])
        if sel_rows:
            target = fdf.iloc[sel_rows[0]]
            st.warning(f"Selected: **{target['author']}** · {target['address']} ({target['county']}) — “{str(target['comment'])[:120]}”")
            if st.button("🗑️ Delete this note (permanent)", type="primary"):
                try:
                    store.delete_comment(target["id"])
                    st.cache_data.clear()
                    st.success("Note deleted.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Delete failed: {e}")
    else:
        st.caption("No notes to moderate.")
