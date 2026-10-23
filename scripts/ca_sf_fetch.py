#!/usr/bin/env python3
"""San Francisco CA recorder (recorder.sfgov.org, Avenu Clearview
Records Manager) grantor/grantee deed-party fetcher — PURE API, no CDP.

SPA hits /SearchService/api/Search/GetSearchResults; auth per call =
GetSecureKey -> {EncryptedKey, Password} headers + empty Bearer.
  - PrimaryLastName=<SURNAME> = exact last-name match (Name= is a noisy
    substring match pulling PATELCO etc)
  - index 1990-present; Rows=100/page, StartRow pager, ~0.8s per page
  - Names field: "(R) GRANTOR NAMES<br/>(E) GRANTEE NAMES" — multi-party
    docs may carry several (R)/(E) segments, names ' and '-joined

DB: data/ca_sf_deeds.db (parties + fetch_log, resume-safe)
"""
import argparse
import json
import random
import re
import sqlite3
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "ca_sf_deeds.db"
API = "https://recorder.sfgov.org/SearchService/api"
HDRS_BASE = {"Accept": "application/json", "User-Agent": "Mozilla/5.0",
             "Referer": "https://recorder.sfgov.org/"}

DDL = """
CREATE TABLE IF NOT EXISTS parties (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_num TEXT, record_date TEXT, doc_type TEXT,
  party_type TEXT, name TEXT, query_surname TEXT,
  fetched_at TEXT DEFAULT (datetime('now')),
  UNIQUE(doc_num, name, party_type, doc_type)
);
CREATE INDEX IF NOT EXISTS idx_parties_name ON parties(name);
CREATE TABLE IF NOT EXISTS fetch_log (
  surname TEXT, yfrom INTEGER, yto INTEGER, status TEXT,
  groups INTEGER DEFAULT 0, rows INTEGER DEFAULT 0,
  PRIMARY KEY (surname, yfrom, yto)
);
"""


def get_key():
    req = urllib.request.Request(f"{API}/SearchConfiguration/GetSecureKey",
                                 headers=HDRS_BASE)
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


def api_search(key, surname, y, start):
    q = urllib.parse.urlencode({
        "DocumentClass": "OfficialRecords", "ProfileID": "Public",
        "PrimaryLastName": surname,
        "MinRecordedDate": f"01/01/{y}", "MaxRecordedDate": f"12/31/{y}",
        "Rows": "100", "StartRow": str(start)})
    req = urllib.request.Request(
        f"{API}/Search/GetSearchResults?{q}",
        headers={**HDRS_BASE, "Authorization": "Bearer",
                 "EncryptedKey": key["EncryptedKey"],
                 "Password": key["Password"]})
    return json.loads(urllib.request.urlopen(req, timeout=60).read())


NAME_RE = re.compile(r"^[A-Z][A-Z &/,'.-]*$")


def parse_names(raw):
    """'(R) X<br/>(E) Y' -> [(party_type, name), ...] per name token."""
    out = []
    for seg in (raw or "").split("<br/>"):
        m = re.match(r"^\((R|E)\)\s*(.*)$", seg.strip())
        if not m:
            continue
        ptype = "GRANTOR" if m.group(1) == "R" else "GRANTEE"
        for tok in re.split(r"\s+and\s+|,", m.group(2)):
            t = tok.strip()
            if len(t) >= 3 and NAME_RE.match(t):
                out.append((ptype, t))
    return out


def one_query(key, surname, y, conn, pace):
    total, rows = 0, []
    start = 0
    while True:
        d = api_search(key, surname, y, start)
        total = int(d.get("ResultCount") or 0)
        for r in d.get("SearchResults") or []:
            doc = r.get("PrimaryDocNumber", "")
            dt = (r.get("FilingCode") or "").replace("<br/>", " | ")
            for ptype, name in parse_names(r.get("Names")):
                if name.split()[0] == surname:
                    rows.append((doc, r.get("DocumentDate", ""), dt,
                                 ptype, name, surname))
        start += 100
        if start >= total or start > 10000:
            break
        time.sleep(0.5 + random.uniform(0, 0.4))
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO parties (doc_num,record_date,doc_type,party_type,name,query_surname) "
            "VALUES (?,?,?,?,?,?)", rows)
    conn.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?,?,?)",
                 (surname, y, y, "ok", total, len(rows)))
    conn.commit()
    time.sleep(pace + random.uniform(0, 0.8))
    return (total, len(rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="1990-2026")
    ap.add_argument("--pace", type=float, default=1.5)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--surnames", default="")
    args = ap.parse_args()
    y0, y1 = (int(x) for x in args.years.split("-"))
    dict_ = json.loads((ROOT / "data" / "hindu_names_dict.json").read_text())
    surnames = sorted(set(dict_.get("hindu_last_names", [])))
    if args.surnames:
        keep = set(args.surnames.upper().split(","))
        surnames = [s for s in surnames if s.upper() in keep]
    conn = sqlite3.connect(DB, timeout=60)
    conn.executescript(DDL)
    conn.commit()
    key, key_age = get_key(), time.time()
    n = 0
    for surname in surnames:
        for y in range(y1, y0 - 1, -1):
            done = conn.execute(
                "SELECT status FROM fetch_log WHERE surname=? AND yfrom=? AND yto=?",
                (surname, y, y)).fetchone()
            if done and done[0] == "ok":
                continue
            for attempt in range(4):
                try:
                    if time.time() - key_age > 600:  # refresh key every 10 min
                        key, key_age = get_key(), time.time()
                    total, nrows = one_query(key, surname, y, conn, args.pace)
                    break
                except Exception as e:
                    print(f"ERR {surname} {y} attempt {attempt}: {e}", flush=True)
                    time.sleep(3)
                    try:
                        key, key_age = get_key(), time.time()
                    except Exception:
                        pass
            n += 1
            if n % 50 == 0:
                tot = conn.execute("SELECT COUNT(*) FROM parties").fetchone()[0]
                print(f"[{n} queries] parties rows={tot}", flush=True)
            if args.limit and n >= args.limit:
                print("limit reached", flush=True)
                return
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
