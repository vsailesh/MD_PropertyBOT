#!/usr/bin/env python3
"""Pull LA County assessor parcel universe (Roll 2025) from the public
Assessor Parcel Data FeatureServer into data/ca_parcels.db.

No owner names in this layer (LA policy) — it's the AIN/situs/coords/usecode
universe that ca_fetch walks per-AIN via the TTC portal.

Resume: uses ca_universe_log(ain) so restarts skip pulled rowIDs.
"""
import argparse
import sqlite3
import sys
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "ca_parcels.db"
FS = ("https://services.arcgis.com/RmCCgQtiZLDCtblq/arcgis/rest/services/"
      "Parcel_Data_2021_Table/FeatureServer/0/query")
FIELDS = ("AIN,RollYear,UseType,UseCode,UseCodeDescChar1,UseCodeDescChar2,"
          "SitusHouseNo,SitusFraction,SitusDirection,SitusStreet,SitusUnit,"
          "SitusCity,SitusZIP5,TaxRateArea_CITY,YearBuilt,SQFTmain,Bedrooms,"
          "Bathrooms,Units,Roll_TotalValue,netTaxableValue,RecordingDate,"
          "CENTER_LAT,CENTER_LON,rowID")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 Chrome/155.0.0.0 Safari/537.36"}


def init_db(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ca_parcels (
      ain TEXT PRIMARY KEY,
      roll_year TEXT, use_type TEXT, use_code TEXT, use_desc1 TEXT, use_desc2 TEXT,
      situs_house TEXT, situs_fraction TEXT, situs_direction TEXT, situs_street TEXT,
      situs_unit TEXT, situs_city TEXT, situs_zip5 TEXT, tra_city TEXT,
      year_built TEXT, sqft_main INTEGER, bedrooms INTEGER, bathrooms INTEGER,
      units INTEGER, roll_total_value INTEGER, net_taxable_value INTEGER,
      recording_date INTEGER, center_lat REAL, center_lon REAL, row_id TEXT UNIQUE
    );
    CREATE INDEX IF NOT EXISTS idx_ca_zip ON ca_parcels(situs_zip5);
    CREATE TABLE IF NOT EXISTS ca_universe_log (
      ain TEXT PRIMARY KEY, pulled_at TEXT DEFAULT (datetime('now'))
    );
    """)


def pull(args):
    conn = sqlite3.connect(DB, timeout=60)
    init_db(conn)
    where = f"RollYear='{args.roll}'"
    if args.residential:
        where += " AND UseCodeDescChar1='Residential'"
    done = conn.execute("SELECT COUNT(*) FROM ca_universe_log").fetchone()[0]
    print(f"resume: {done} rows already logged", flush=True)
    last_rowid = done
    n = 0
    t0 = time.time()
    while True:
        params = {
            "where": where, "outFields": FIELDS,
            "orderByFields": "rowID", "resultOffset": last_rowid,
            "resultRecordCount": 2000, "f": "json",
        }
        for attempt in range(5):
            try:
                r = requests.get(FS, params=params, headers=UA, timeout=180)
                d = r.json()
                if "features" in d:
                    break
            except Exception as e:
                print(f"req err attempt {attempt}: {e}", flush=True)
                time.sleep(5 * (attempt + 1))
                d = {}
        feats = d.get("features")
        if not feats:
            print(f"no more features (exceededTransferLimit={d.get('exceededTransferLimit')}), stop",
                  flush=True)
            break
        rows, ains = [], []
        for f in feats:
            a = f["attributes"]
            ain = a.get("AIN")
            rows.append((
                ain, a.get("RollYear"), a.get("UseType"), a.get("UseCode"),
                a.get("UseCodeDescChar1"), a.get("UseCodeDescChar2"),
                a.get("SitusHouseNo"), a.get("SitusFraction"), a.get("SitusDirection"),
                a.get("SitusStreet"), a.get("SitusUnit"), a.get("SitusCity"),
                a.get("SitusZIP5"), a.get("TaxRateArea_CITY"), a.get("YearBuilt"),
                a.get("SQFTmain"), a.get("Bedrooms"), a.get("Bathrooms"),
                a.get("Units"), a.get("Roll_TotalValue"), a.get("netTaxableValue"),
                a.get("RecordingDate"), a.get("CENTER_LAT"), a.get("CENTER_LON"),
                a.get("rowID")))
            ains.append((ain,))
        conn.executemany(
            "INSERT OR IGNORE INTO ca_parcels VALUES (" + ",".join("?" * 25) + ")", rows)
        conn.executemany("INSERT OR IGNORE INTO ca_universe_log VALUES (?, datetime('now'))", ains)
        conn.commit()
        last_rowid += len(feats)
        n += len(feats)
        if n % 20000 < 2000:
            rate = n / (time.time() - t0 + 1)
            eta_h = (args.expect - done - n) / max(rate, 0.1) / 3600
            print(f"{done + n} rows | {rate:.0f}/s | eta {eta_h:.1f}h", flush=True)
        if not d.get("exceededTransferLimit"):
            print("complete: transfer limit not exceeded", flush=True)
            break
        time.sleep(args.pace)
    conn.close()
    print(f"TOTAL pulled this run: {n}", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--roll", default="2025")
    ap.add_argument("--residential", action="store_true", default=True)
    ap.add_argument("--expect", type=int, default=2153246)
    ap.add_argument("--pace", type=float, default=0.3)
    args = ap.parse_args()
    pull(args)
