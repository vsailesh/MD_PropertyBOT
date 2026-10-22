#!/usr/bin/env python3
"""Orange County CA recorder (cr.occlerkrecorder.gov/RecorderWorksInternet)
grantor/grantee deed-party fetcher via CDP organic Chrome
(port 9227, profile /tmp/claude-501/oc_profile).

OC = RecorderWorks WebForms app. Free name search, no TOC, no prename
treeview (unlike SD ACCLAIM). Party roles come back per-doc in separate
containers, so both grantor and grantee lists land in one query.

Flow per (surname, year):
  fill partytype=All + nameForSearch + FromDate/ToDate -> btnSearch div click
  -> parse doc rows ([id=EntityTitleDocNum_resTable]) -> follow pager
     (search.OnPage / Next cell until pagingCellDisable).

DB: data/ca_oc_deeds.db (parties + fetch_log, resume-safe)
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
DB = ROOT / "data" / "ca_oc_deeds.db"
BASE = "https://cr.occlerkrecorder.gov/RecorderWorksInternet/"
CDP = "http://localhost:9227"

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
        tab = next((t for t in tabs if "occlerkrecorder" in (t.get("url") or "")), None)
        if not tab:
            req = urllib.request.Request(f"{CDP}/json/new?{BASE}", method="PUT")
            tab = json.load(urllib.request.urlopen(req))
            time.sleep(8)
        self.ws = websocket.create_connection(
            tab["webSocketDebuggerUrl"], timeout=60, origin="http://localhost:9227")
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
        res = r.get("result", {}).get("result", {})
        return res.get("value")

    def nav(self, url, wait=6):
        self.cmd("Page.navigate", {"url": url})
        time.sleep(wait)

    def reconnect(self):
        try:
            self.ws.close()
        except Exception:
            pass
        time.sleep(2)
        self.__init__()


FILL = """(()=>{
  const p=document.getElementById('MainContent_MainMenu1_SearchByName1_partytype');
  if(!p) return 'noform';
  p.value='0'; p.dispatchEvent(new Event('change',{bubbles:true}));
  const nm=document.getElementById('MainContent_MainMenu1_SearchByName1_nameForSearch');
  nm.value=%r; nm.dispatchEvent(new Event('input',{bubbles:true}));
  const f=document.getElementById('MainContent_MainMenu1_SearchByName1_FromDate');
  f.value=%r; f.dispatchEvent(new Event('input',{bubbles:true}));
  const t=document.getElementById('MainContent_MainMenu1_SearchByName1_ToDate');
  t.value=%r; t.dispatchEvent(new Event('input',{bubbles:true}));
  return 'set';
})()"""

SEARCH_CLICK = "document.getElementById('MainContent_MainMenu1_SearchByName1_btnSearch').click()"

RESULT_COUNT = """(()=>{const e=document.getElementById('SearchResultsTitle1_resultCount'); return e?parseInt(e.innerText.replace(/[^0-9]/g,''))||0:0;})()"""

GRID_ROWS = """(()=>{
  const out=[];
  document.querySelectorAll('[id=EntityTitleDocNum_resTable]').forEach(t=>{
    const tr=t.closest('tr'); if(!tr) return;
    const tds=[...tr.querySelectorAll(':scope > td')];
    if(tds.length<4) return;
    const doc=(tds[1].innerText||'').trim();
    const cell=tds[2];
    const grab=c=>[...cell.querySelectorAll('.'+c+' p')].map(p=>(p.innerText||'').trim()).filter(Boolean);
    const row={doc:doc, date:(tds[3].innerText||'').trim(),
      grt:grab('GrtContainer'), grtee:grab('GrteeContainer'), both:grab('GrGrteeContainer'),
      dt:grab('DocTypeContainer').map(s=>s.trim()).filter(Boolean)};
    out.push(JSON.stringify(row));
  });
  return out.join('\\n');
})()"""

NEXT_PAGE = """(()=>{
  const cells=[...document.querySelectorAll('#SearchResultsTitle1_paging .pagingCell')];
  const nx=cells.find(c=>(c.innerText||'').trim()==='Next');
  if(!nx||nx.className.includes('pagingCellDisable')) return 0;
  nx.click(); return 1;
})()"""

FIRST_DOC = """(()=>{
  const t=document.querySelector('[id=EntityTitleDocNum_resTable]');
  return t?(t.closest('tr').querySelectorAll(':scope > td')[1].innerText||'').trim():'';
})()"""


def parse_rows(raw, surname):
    rows = []
    for line in raw.splitlines():
        try:
            r = json.loads(line)
        except Exception:
            continue
        doc = r.get("doc", "")
        if not re.match(r"^\d{4}\d+$", doc):
            continue
        # stale-grid guard: doc must involve the query surname
        names = r.get("grt", []) + r.get("grtee", []) + r.get("both", [])
        if not any(surname in n.upper() for n in names):
            continue
        date, dt = r.get("date", ""), (r.get("dt") or [""])[0]
        for role, key in (("GRANTOR", "grt"), ("GRANTEE", "grtee"), ("BOTH", "both")):
            for n in r.get(key, []):
                if n:
                    rows.append((doc, date, dt, role, n, surname))
    return rows


def one_query(tab, surname, dfrom, dto, conn, pace):
    """One name+daterange search; returns (result_count, nrows)."""
    r = tab.ev(FILL % (surname, dfrom, dto))
    if r != "set":
        tab.nav(BASE, 6)
        r = tab.ev(FILL % (surname, dfrom, dto))
        if r != "set":
            raise RuntimeError(f"form fill failed: {r}")
    first_doc_before = tab.ev(FIRST_DOC)
    tab.ev(SEARCH_CLICK)
    # poll for fresh grid (stale grid persists between queries)
    total = tab.ev(RESULT_COUNT)
    deadline = time.time() + 20
    while time.time() < deadline:
        if tab.ev(RESULT_COUNT) > 0 and tab.ev(FIRST_DOC) != first_doc_before:
            break
        time.sleep(1.5)
    total = tab.ev(RESULT_COUNT) or 0
    all_rows = []
    page = 0
    while True:
        raw = tab.ev(GRID_ROWS)
        rows = parse_rows(raw, surname)
        if page == 0 and not rows:
            break  # no (or stale) results — never page stale grids
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO parties (doc_num,record_date,doc_type,party_type,name,query_surname) "
                "VALUES (?,?,?,?,?,?)", rows)
            all_rows += rows
        page += 1
        if not tab.ev(NEXT_PAGE) or page > 250:
            break
        for _ in range(15):
            time.sleep(1.5)
            if tab.ev(FIRST_DOC):
                break
    conn.commit()
    time.sleep(pace + random.uniform(0, 1.5))
    return (total, len(all_rows))


def one_surname_year(tab, surname, y, conn, pace):
    """Full-year query; splits halves if result count huge (unknown site cap)."""
    to = f"12/31/{y}" if y < time.localtime().tm_year else time.strftime("%m/%d/%Y")
    total, nrows = one_query(tab, surname, f"01/01/{y}", to, conn, pace)
    if total > 4000:
        # split half-years, re-run (rows dedup via UNIQUE)
        for mf, mt in (("01/01", "06/30"), ("07/01", "12/31")):
            ht = f"{mt}/{y}" if y < time.localtime().tm_year else min(
                f"{mt}/{y}", time.strftime("%m/%d/%Y"))
            _, hrows = one_query(tab, surname, f"{mf}/{y}", ht, conn, pace)
            nrows += hrows
        nrows = -nrows  # negative marks split queries in log
    return total, nrows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2010-2026")
    ap.add_argument("--pace", type=float, default=4.0)
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
                    total, nrows = one_surname_year(tab, surname, y, conn, args.pace)
                    conn.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?,?,?)",
                                 (surname, y, y, "ok", total, nrows))
                    conn.commit()
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
