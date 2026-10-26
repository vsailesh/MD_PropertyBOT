#!/usr/bin/env python3
"""Export hindu deed-party owners across CA county recorder DBs to Excel.

Deed parties have no parcels/coords (AB 1785 scrubbed those), so this is a
name-level export: one row per unique (name, county) with deed counts,
grantor/grantee split, and first/last recording dates. Rows are hindu by
construction (walkers query hindu surnames and filter first-token match)
but we re-verify against the dict to drop parse junk (LLC/numeric rows).

Usage: python scripts/ca_export.py [-o data/CA_Hindu_Owners.xlsx]
"""
import argparse
import json
import os
import sqlite3
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from geocode_results import map_surname_to_origin  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# county label -> db filename
COUNTIES = {
    "San Diego": "ca_sd_deeds.db",
    "Orange": "ca_oc_deeds.db",
    "Ventura": "ca_vc_deeds.db",
    "San Bernardino": "ca_sb_deeds.db",
    "Fresno": "ca_fresno_deeds.db",
    "Tulare": "ca_tulare_deeds.db",
    "Stanislaus": "ca_stan_deeds.db",
    "San Joaquin": "ca_sj_deeds.db",
    "Santa Barbara": "ca_sbcrec_deeds.db",
    "San Mateo": "ca_smc_deeds.db",
    "Sonoma": "ca_sonoma_deeds.db",
    "San Francisco": "ca_sf_deeds.db",
    "Placer": "ca_placer_deeds.db",
    "San Luis Obispo": "ca_slo_deeds.db",
    "Santa Cruz": "ca_scr_deeds.db",
    "Yolo": "ca_yolo_deeds.db",
}

STOP_TOKENS = {"LLC", "INC", "LTD", "CORP", "TRUST", "CO", "COMPANY"}


def to_iso(d):
    """MM/DD/YYYY -> YYYY-MM-DD (dates arrive in recorder-local format)."""
    if not d:
        return ""
    p = d.split("/")
    return f"{p[2]}-{p[0]:>0s}-{p[1]:>0s}" if len(p) == 3 else d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", default=os.path.join(ROOT, "data", "CA_Hindu_Owners.xlsx"))
    args = ap.parse_args()

    dict_ = json.loads(open(os.path.join(ROOT, "data", "hindu_names_dict.json")).read())
    DICT_SUR = {s.upper() for s in dict_.get("hindu_last_names", [])}
    FIRSTS = (set(dict_.get("hindu_first_names", []))
              | set(dict_.get("distinctive_first_names", []))
              | set(dict_.get("sikh_names", [])))
    BUSINESS = (set(dict_.get("business_markers", []))
                | set(dict_.get("noise_words", [])))

    frames = []
    for county, db in COUNTIES.items():
        path = os.path.join(ROOT, "data", db)
        if not os.path.exists(path):
            continue
        conn = sqlite3.connect(path)
        df = pd.read_sql_query(
            "SELECT name, party_type, record_date, doc_num FROM parties", conn)
        conn.close()
        df["county"] = county
        frames.append(df)
        print(f"{county:16s} {len(df):>7d} party rows")
    raw = pd.concat(frames, ignore_index=True)
    print(f"total party rows: {len(raw)}")

    # classification per unique surname token (fast), then per-row verify:
    #  - surname in dict -> hindu
    #  - surname is a prefix variant of a dict surname (BHATTI <- BHAT,
    #    pulled in by Tyler prefix-match queries) -> hindu ONLY when a
    #    hindu given name corroborates on the same row (BASURTO MARIA
    #    dies, BHATTI AMARJIT passes). Business/noise tokens kill a row.
    def classify(name):
        toks = [t.strip(",").upper() for t in str(name).split() if t.strip(",")]
        if not toks or any(t in BUSINESS for t in toks):
            return ""
        sur = toks[0]
        if sur in DICT_SUR:
            return sur
        if len(sur) >= 4:
            for d in DICT_SUR:
                if len(d) >= 4 and (sur.startswith(d) or d.startswith(sur)) \
                        and any(t in FIRSTS for t in toks[1:]):
                    return d
        return ""

    uniq = {n: classify(n) for n in raw["name"].unique()}
    raw["surname"] = raw["name"].map(uniq)
    raw = raw[raw["surname"] != ""]
    print(f"after dict/variant filter: {len(raw)}")

    def iso(d):
        p = str(d).split("/")
        return f"{p[2]}-{int(p[0]):02d}-{int(p[1]):02d}" if len(p) == 3 else str(d)

    raw["date_iso"] = raw["record_date"].map(iso)
    raw["is_grantor"] = (raw["party_type"] == "GRANTOR").astype(int)

    grp = raw.groupby(["name", "county", "surname"], as_index=False).agg(
        deeds=("doc_num", "count"),
        docs=("doc_num", "nunique"),
        grantor=("is_grantor", "sum"),
        first_deed=("date_iso", "min"),
        last_deed=("date_iso", "max"),
    )
    grp["grantee"] = grp["deeds"] - grp["grantor"]
    grp["state"] = "CA"
    grp["region"] = grp["surname"].map(
        lambda s: map_surname_to_origin(s)["region"])
    out = grp.rename(columns={
        "name": "Owner Name", "surname": "Surname", "county": "County",
        "state": "State", "deeds": "Deed Records", "docs": "Distinct Docs",
        "grantor": "As Grantor", "grantee": "As Grantee",
        "first_deed": "First Deed", "last_deed": "Last Deed",
        "region": "Origin Region",
    })[["Owner Name", "Surname", "Origin Region", "County", "State",
        "Deed Records", "Distinct Docs", "As Grantor", "As Grantee",
        "First Deed", "Last Deed"]] \
        .sort_values(["County", "Owner Name"])

    out.to_excel(args.o, index=False)
    print(f"{len(out)} owner rows -> {args.o}")
    print(out.groupby("County").size().to_string())


if __name__ == "__main__":
    main()
