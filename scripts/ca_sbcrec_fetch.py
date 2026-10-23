#!/usr/bin/env python3
"""Santa Barbara County CA recorder (fresnocountyca-web.tylerhost.net)
grantor/grantee deed-party fetcher via CDP organic Chrome
(port 9229, profile /tmp/claude-501/sbcrec_profile).

Tyler self-service platform, same family as Ventura but:
  - NO typeahead chips — bare surname typed straight into
    #field_BothNamesID; search = anchor with text 'Search' (no id)
  - queries are PREFIX match (PATEL -> PATEL*) -> Clear Selections
    between queries
  - docnums carry a dash: 2024-0310581
  - results 100/page pager 'Next'; 'Showing page N of M for K Total
    Results'; blocks: <docnum> • TYPE / Recording Date / date-time /
    Grantor (n) / names / Grantee (n) / names

Disclaimer reCAPTCHA v2: solved ONCE per session via
scripts/ca_captcha_auto.py (fully-local YOLOv8n+OCR loop); cookie
persists while runner keeps chrome alive. rc=2 = gate returned.

DB: data/ca_sbcrec_deeds.db (parties + fetch_log, resume-safe)
"""
import argparse
import json
import random
import re
import sqlite3
import sys
import time
import urllib.request
from pathlib import Path

import websocket

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "ca_sbcrec_deeds.db"
BASE = "https://records.sbcrecorder.com"
SEARCH = f"{BASE}/web/search/DOCSEARCH439S1"
CDP = "http://localhost:9234"

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


class Tab:
    def __init__(self):
        tabs = json.load(urllib.request.urlopen(f"{CDP}/json"))
        tab = next((t for t in tabs if "sbcrecorder" in (t.get("url") or "")), None)
        if not tab:
            req = urllib.request.Request(f"{CDP}/json/new?{SEARCH}", method="PUT")
            tab = json.load(urllib.request.urlopen(req))
            time.sleep(8)
        self.ws = websocket.create_connection(
            tab["webSocketDebuggerUrl"], timeout=90, origin="http://localhost:9234")
        self.n = 0

    def cmd(self, method, params=None):
        self.n += 1
        i = self.n
        self.ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == i:
                return m

    def ev(self, expression):
        r = self.cmd("Runtime.evaluate", {"expression": expression,
                                          "returnByValue": True})
        return r.get("result", {}).get("result", {}).get("value")

    def evj(self, expression):
        v = self.ev(expression)
        if v is None or isinstance(v, list):
            return v
        try:
            return json.loads(v)
        except Exception:
            return None

    def nav(self, url, wait=7):
        self.cmd("Page.navigate", {"url": url})
        time.sleep(wait)

    def reconnect(self):
        try:
            self.ws.close()
        except Exception:
            pass
        time.sleep(2)
        self.__init__()

    def click(self, cx, cy):
        for evt in [{"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1},
                    {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1}]:
            self.cmd("Input.dispatchMouseEvent", evt)
        time.sleep(0.4)


def center_js(sel):
    return f"""(()=>{{const e=document.querySelector('{sel}'); if(!e||!e.offsetParent) return null; e.scrollIntoView({{block:'center'}}); const r=e.getBoundingClientRect(); return [r.x+r.width/2,r.y+r.height/2];}})()"""


DOC_RE = re.compile(r"(\d{4}-\d{6,8})\s*•\s*([A-Z][A-Z &/,'.-]{2,60})")
TOTAL_RE = re.compile(r"Showing page \d+ of (\d+) for ([\d,]+) Total Results")
NEXT_JS = """(()=>{
  const a=[...document.querySelectorAll('a')].find(e=>e.offsetParent&&(e.innerText||'').trim()==='Next');
  if(!a) return null; a.scrollIntoView({block:'center'});
  const r=a.getBoundingClientRect(); return [r.x+r.width/2, r.y+r.height/2];
})()"""
CLEAR_JS = """(()=>{
  const a=document.getElementById('clearSearchButton');
  if(!a) return false; a.click(); return true;
})()"""


def parse_body(body, surname):
    """Parse result text blocks into party rows (surname prefix-filtered)."""
    rows = []
    hits = list(DOC_RE.finditer(body))
    for i, m in enumerate(hits):
        doc, doctype = m.group(1), m.group(2).strip()
        seg = body[m.end(): hits[i + 1].start() if i + 1 < len(hits) else len(body)]
        dm = re.search(r"Recording Date\s*\n\s*([01]\d/[0-3]\d/\d{4})", seg)
        date = dm.group(1) if dm else ""
        g = re.search(r"Grantor(?:\s*\(\d+\))?\s*\n(.*?)(?=Grantee)", seg, re.S)
        ge = re.search(r"Grantee(?:\s*\(\d+\))?\s*\n(.*?)$", seg, re.S)

        def names(txt):
            out = []
            for line in (txt or "").split("\n"):
                t = line.strip()
                if len(t) >= 3 and re.match(r"^[A-Z][A-Z &/,'.-]*$", t) \
                        and not re.match(r"^Recording|^Filter|^Format", t):
                    out.append(t)
            return out

        for n in names(g.group(1) if g else ""):
            if n.startswith(surname):
                rows.append((doc, date, doctype, "GRANTOR", n, surname))
        for n in names(ge.group(1) if ge else ""):
            if n.startswith(surname):
                rows.append((doc, date, doctype, "GRANTEE", n, surname))
    return rows


def save_rows(conn, rows):
    if rows:
        conn.executemany(
            "INSERT OR IGNORE INTO parties (doc_num,record_date,doc_type,party_type,name,query_surname) "
            "VALUES (?,?,?,?,?,?)", rows)


def one_query(tab, surname, y, conn, pace):
    tab.nav(SEARCH, 7)
    body = tab.ev("document.body.innerText") or ""
    if "Disclaimer" in body and "I Accept" in body:
        return ("CAPTCHA", 0)
    # clear any previous chips/selections
    if "Total Results" in body or tab.ev(
            "(()=>{const n=document.getElementById('field_BothNamesID'); return n? n.value.length:0;})()"):
        tab.ev(CLEAR_JS)
        time.sleep(2)
    # name field: click + type (SB: no typeahead chips, direct entry;
    # Tyler renders async — poll up to 20s for the field)
    c = None
    for _ in range(14):
        c = tab.evj(center_js("#field_BothNamesID"))
        if c:
            break
        time.sleep(1.5)
    if not c:
        raise RuntimeError("no name field")
    tab.click(*c)
    tab.cmd("Input.insertText", {"text": surname})
    time.sleep(0.8)
    for sel, val in [("#field_RecordingDateID_DOT_StartDate", f"01/01/{y}"),
                     ("#field_RecordingDateID_DOT_EndDate", f"12/31/{y}")]:
        c = tab.evj(center_js(sel))
        if not c:
            raise RuntimeError(f"no field {sel}")
        tab.click(*c)
        tab.cmd("Input.insertText", {"text": val})
    time.sleep(0.6)
    sb = tab.evj("""(()=>{const a=[...document.querySelectorAll('a')].find(e=>e.offsetParent&&/^(Search|SEARCH)$/.test((e.innerText||'').trim())); if(!a) return null; a.scrollIntoView({block:'center'}); const r=a.getBoundingClientRect(); return [r.x+r.width/2,r.y+r.height/2];})()""")
    if not sb:
        raise RuntimeError("no search button")
    tab.click(*sb)
    # first page of results
    body = ""
    for _ in range(12):
        time.sleep(1.5)
        body = tab.ev("document.body.innerText") or ""
        if "Total Results" in body:
            break
    tm = TOTAL_RE.search(body)
    if not tm:
        # no results at all: zero rows
        conn.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?,?,?)",
                     (surname, y, y, "ok", 0, 0))
        conn.commit()
        time.sleep(pace + random.uniform(0, 1.2))
        return (0, 0)
    total_rows = parse_body(body, surname)
    npages = int(tm.group(1))
    # pager loop
    page = 1
    while page < npages and page < 100:
        nb = tab.evj(NEXT_JS)
        if not nb:
            break
        tab.click(*nb)
        pbody = ""
        for _ in range(10):
            time.sleep(1.5)
            pbody = tab.ev("document.body.innerText") or ""
            if TOTAL_RE.search(pbody) and pbody != body:
                break
        if pbody == body:
            break  # no progress
        body = pbody
        total_rows += parse_body(body, surname)
        page += 1
    save_rows(conn, total_rows)
    total = int(tm.group(2).replace(",", "")) if tm else 0
    conn.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?,?,?)",
                 (surname, y, y, "ok", total, len(total_rows)))
    conn.commit()
    time.sleep(pace + random.uniform(0, 1.2))
    return (total, len(total_rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2015-2026")
    ap.add_argument("--pace", type=float, default=5.0)
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
    tab = Tab()
    n = 0
    for surname in surnames:
        for y in range(y1, y0 - 1, -1):
            done = conn.execute(
                "SELECT status FROM fetch_log WHERE surname=? AND yfrom=? AND yto=?",
                (surname, y, y)).fetchone()
            if done and done[0] == "ok":
                continue
            for attempt in range(3):
                try:
                    total, nrows = one_query(tab, surname, y, conn, args.pace)
                    if total == "CAPTCHA":
                        print("CAPTCHA gate hit — needs solver/manual, aborting", flush=True)
                        sys.exit(2)
                    break
                except Exception as e:
                    print(f"ERR {surname} {y} attempt {attempt}: {e}", flush=True)
                    tab.reconnect()
            n += 1
            if n % 25 == 0:
                tot = conn.execute("SELECT COUNT(*) FROM parties").fetchone()[0]
                print(f"[{n} queries] parties rows={tot}", flush=True)
            if args.limit and n >= args.limit:
                print("limit reached", flush=True)
                return
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
