"""Outreach Review — every note in one place for editors.

All comments from Supabase with filters (author, type, county, date, text
search), quick stats, and CSV export. Editors only; readers get a sign-in
prompt.
"""
import os
import sys

import pandas as pd
import streamlit as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from comments_store import CommentsStore
from auth_gate import current_user, is_editor

st.set_page_config(page_title="Outreach Review", layout="wide", page_icon="📋")
st.title("📋 Outreach Review")
st.caption("Every outreach note in one place — filter, review quality, export.")

user = current_user()
if user and is_editor(user) and not st.session_state.get("_login_logged_review"):
    st.session_state._login_logged_review = True
    CommentsStore().log_auth_event(user["email"], user["name"], "review")
if not (user and is_editor(user)):
    st.info("Editors only. Sign in with Google on the main dashboard page, then come back here.")
    st.stop()

store = CommentsStore()
if not store.enabled:
    st.error("Comments backend not configured. Set SUPABASE_URL / SUPABASE_ANON_KEY (see README).")
    st.stop()

try:
    rows = store.all_comments()
except Exception as e:
    st.error(f"Could not load notes: {e}")
    st.stop()

if not rows:
    st.success("No outreach notes yet — they'll appear here as editors add them.")
    st.stop()

df = pd.DataFrame(rows)
for col in ("county", "address", "author", "outreach_type", "comment"):
    if col not in df.columns:
        df[col] = ""
df["created_at"] = pd.to_datetime(df["created_at"], errors="coerce")
df["outreach_date"] = pd.to_datetime(df.get("outreach_date"), errors="coerce")

# ------------------------------------------------------------ filters
with st.sidebar:
    st.header("🔍 Filters")

    authors = sorted(df["author"].dropna().unique().tolist())
    sel_authors = st.multiselect("Author", authors, default=authors)

    types = sorted(df["outreach_type"].dropna().unique().tolist())
    sel_types = st.multiselect("Outreach type", types, default=types)

    counties = sorted(df["county"].dropna().unique().tolist())
    sel_counties = st.multiselect("County", counties, default=counties)

    drange = st.date_input(
        "Created between",
        value=(df["created_at"].min().date(), df["created_at"].max().date()),
    )
    if isinstance(drange, tuple) and len(drange) == 2:
        dmin, dmax = drange
    else:  # cleared / half-picked — fall back to full range
        dmin, dmax = df["created_at"].min().date(), df["created_at"].max().date()
    q = st.text_input("Text contains (note / address / county)").strip().lower()

mask = (
    df["author"].isin(sel_authors)
    & df["outreach_type"].isin(sel_types)
    & df["county"].isin(sel_counties)
    & df["created_at"].dt.date.between(dmin, dmax)
)
if q:
    joined = (df["comment"].astype(str) + " " + df["address"].astype(str) + " "
              + df["county"].astype(str)).str.lower()
    mask &= joined.str.contains(q, regex=False)
fdf = df[mask]

# ------------------------------------------------------------ stats
c1, c2, c3, c4 = st.columns(4)
c1.metric("Notes", f"{len(fdf):,}")
c2.metric("Properties touched", f"{fdf.groupby(['county_key', 'address_key']).ngroups:,}")
c3.metric("Active authors", f"{fdf['author'].nunique():,}")
week_ago = pd.Timestamp.now() - pd.Timedelta(days=7)
c4.metric("Added last 7 days", f"{len(fdf[fdf['created_at'] >= week_ago]):,}")

if len(fdf) != len(df):
    st.caption(f"Showing {len(fdf):,} of {len(df):,} notes.")

tab_notes, tab_stats = st.tabs(["🗒️ Notes", "📊 Breakdown"])

with tab_notes:
    st.dataframe(
        fdf[["created_at", "outreach_date", "author", "outreach_type",
             "county", "address", "comment"]],
        column_config={
            "created_at": st.column_config.DatetimeColumn("Logged", format="YYYY-MM-DD HH:mm"),
            "outreach_date": st.column_config.DateColumn("Outreach date", format="YYYY-MM-DD"),
            "author": st.column_config.TextColumn("Author"),
            "outreach_type": st.column_config.TextColumn("Type"),
            "county": st.column_config.TextColumn("County"),
            "address": st.column_config.TextColumn("Address"),
            "comment": st.column_config.TextColumn("Note", width="large"),
        },
        hide_index=True,
        use_container_width=True,
        height=480,
    )

with tab_stats:
    cc1, cc2 = st.columns(2)
    with cc1:
        st.subheader("By author")
        st.bar_chart(fdf["author"].value_counts())
    with cc2:
        st.subheader("By type")
        st.bar_chart(fdf["outreach_type"].value_counts())
    st.subheader("By county")
    st.bar_chart(fdf["county"].value_counts())

st.download_button(
    label="📥 Export filtered notes (CSV)",
    data=fdf[["created_at", "outreach_date", "author", "outreach_type",
              "county", "address", "comment"]].to_csv(index=False).encode("utf-8"),
    file_name="outreach_notes_export.csv",
    mime="text/csv",
)
