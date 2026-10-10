"""Coverage — statewide scrape progress vs the parcel universe.

Denominators come from the MDP parcel store (data/md_bulk.db, Socrata
ed4q-f8tm: 2.44M parcels), not the TIGER-derived input list — see
scripts/coverage_report.py for the methodology. "Covered" means a
completed SDAT search that actually returned data.

Public aggregate stats — no sign-in required. Cached 15 min (the full
computation walks both stores, ~1 min).
"""
import os
import sys

import pandas as pd
import streamlit as st

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

try:
    from coverage_report import compute_coverage  # noqa: E402
except ImportError:
    # streamlit's page runner doesn't always honor the path inserts above
    import importlib.util
    _spec = importlib.util.spec_from_file_location(
        "coverage_report",
        os.path.join(ROOT, "scripts", "coverage_report.py"))
    _cr = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(_cr)
    compute_coverage = _cr.compute_coverage

st.set_page_config(page_title="Coverage", layout="wide", page_icon="📊")
st.title("📊 Statewide Coverage")
st.caption("SDAT scrape progress measured against the MDP parcel universe "
           "(every street/parcel with a situs address). Refreshed every 15 min.")


@st.cache_data(ttl=900, show_spinner="Computing coverage…")
def load_coverage():
    return compute_coverage(per_county=True)


@st.cache_data(ttl=900, show_spinner="Reading queue…")
def load_queue():
    import sqlite3
    conn = sqlite3.connect(os.path.join(ROOT, "data", "property_search.db"))
    df = pd.read_sql_query(
        """SELECT b.id, b.batch_name, b.status, b.total_streets,
                  SUM(CASE WHEN sp.status = 'pending' THEN 1 ELSE 0 END) AS pending,
                  SUM(CASE WHEN sp.status IN ('completed','failed') THEN 1 ELSE 0 END) AS done
           FROM batches b JOIN search_progress sp ON sp.batch_id = b.id
           WHERE b.status IN ('in_progress', 'interrupted')
           GROUP BY b.id ORDER BY b.id ASC""",
        conn)
    conn.close()
    return df


r = load_coverage()
s, p = r["streets"], r["properties"]

m1, m2, m3, m4 = st.columns(4)
m1.metric("Street coverage", f"{s['coverage_pct']}%",
          f"{s['scraped_completed_in_universe']:,} / {s['universe_parcels_streets']:,}")
m2.metric("Parcels w/ owner", f"{p['addr_join_coverage_pct']}%",
          f"{p['parcels_with_owner_via_addr_join']:,} / {p['universe_parcels']:,}")
m3.metric("Owner records", f"{p['scraped_rows']:,}",
          "distinct county/owner/address rows")
m4.metric("Parcels w/ owner (exact)", f"{p['exact_account_coverage_pct'] or 0}%",
          f"{p['exact_account_matched'] or 0:,} / {p['universe_parcels']:,} via account_id")

st.divider()

queue = load_queue()
left, right = st.columns([3, 2])

with left:
    st.subheader("Per-county street coverage")
    df = pd.DataFrame([
        {"County": c, "Streets": d["universe"],
         "Scraped": d["scraped"], "Coverage %": d["pct"]}
        for c, d in r["per_county"].items()
    ])
    st.dataframe(df, use_container_width=True, height=520,
                 column_config={
                     "Coverage %": st.column_config.ProgressColumn(
                         "Coverage %", min_value=0, max_value=100,
                         format="%.1f%%"),
                 })

with right:
    st.subheader("Scrape queue")
    if queue.empty:
        st.success("Queue empty — everything drained.")
    else:
        st.dataframe(queue.rename(columns={
            "id": "Batch", "batch_name": "Name", "status": "Status",
            "total_streets": "Total", "pending": "Pending", "done": "Done"}),
            use_container_width=True, hide_index=True)
        pending = int(queue["pending"].sum())
        # engine pace: ~10 s/street inside the 01:00-06:00 window ≈ 1,500/night
        nights = max(1, -(-pending // 1500))
        st.info(f"**{pending:,} streets pending** — browser engine drains "
                f"~1,500/night (01:00–06:00 window) ≈ **{nights} more "
                f"night{'s' if nights > 1 else ''}** to clear the queue.")

st.divider()
st.markdown(
    "**Method:** street universe = distinct street names with ≥1 parcel in the "
    "MDP parcel store (Socrata `ed4q-f8tm`); covered = completed SDAT search "
    "that returned data. Parcel ownership = scraped addresses joined to parcels "
    "by (county, street, house number); exact parcel keys (`map_parcel`) land "
    "as the browser engine's rotations backfill them.")
