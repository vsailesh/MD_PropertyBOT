#!/usr/bin/env python3
"""SD ACCLAIM (arcc-acclaim.sdcounty.ca.gov) grantor/grantee deed-party
fetcher via CDP organic Chrome (port 9225, profile /tmp/claude-501/acclaim_profile).

CA assessor portals scrub owner names, but the county RECORDER name search
(1970+) still works. Surname enumeration -> deed parties (buyer/seller,
doc#, date, doctype). No APN online (AB 1785 since 12/2024), so rows are
deed events, not parcels.

Flow per (surname, year):
  SearchTypeName -> fill (Both + name + dates) -> btnSearch
  -> prename treeview appears if >1 name grouping:
     SelectAllOrNoneInTreeView(true) + GetNameListString + Done.click
  -> parse results table, follow pager.

DB: data/ca_sd_deeds.db  (parties + fetch_log, resume-safe)
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
DB = ROOT / "data" / "ca_sd_deeds.db"
BASE = "https://arcc-acclaim.sdcounty.ca.gov"
CDP = "http://localhost:9225"

DDL = """
CREATE TABLE IF NOT EXISTS parties (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_num TEXT, record_date TEXT, doc_type TEXT,
  party_type TEXT, name TEXT, cross_party TEXT, query_surname TEXT,
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
    def __init__(self, filter_url="acclaim"):
        tabs = json.load(urllib.request.urlopen(f"{CDP}/json"))
        tab = next((t for t in tabs if filter_url in (t.get("url") or "")), None)
        if not tab:
            req = urllib.request.Request(f"{CDP}/json/new?{BASE}/", method="PUT")
            tab = json.load(urllib.request.urlopen(req))
            time.sleep(6)
        self.ws = websocket.create_connection(
            tab["webSocketDebuggerUrl"], timeout=60, origin="http://localhost:9225")
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
  const b=document.getElementById('btnButton'); if(b){b.click(); return 'toc';}
  const both=document.getElementById('Both'); if(both) both.click();
  const n=document.getElementById('SearchOnName'); if(!n) return 'noform';
  n.value=%r; n.dispatchEvent(new Event('input',{bubbles:true}));
  const f=document.getElementById('RecordDateFrom'); f.value=%r;
  f.dispatchEvent(new Event('input',{bubbles:true}));
  const t=document.getElementById('RecordDateTo'); t.value=%r;
  t.dispatchEvent(new Event('input',{bubbles:true}));
  return 'set';
})()"""

PREENAME_DONE = """(()=>{
  const tv=document.getElementById('NameListTreeView');
  if(!tv) return 'nopre';
  const t=document.querySelector('input[name=SelectAllPrenamesToggle]');
  if(t){t.checked=true;}
  SelectAllOrNoneInTreeView('NameListTreeView', true);
  GetNameListString('NameListTreeView');
  const done=[...document.querySelectorAll('input[type=submit]')].find(b=>b.value==='Done');
  if(!done) return 'nodone';
  done.click(); return 'done';
})()"""

GRID_ROWS = """(()=>{
  const cands=[...document.querySelectorAll('table')].filter(e=>e.innerHTML.includes('checkIsPurchased'));
  const tbl=cands.sort((a,b)=>a.innerHTML.length-b.innerHTML.length)[0];
  if(!tbl) return '';
  const out=[];
  for(const tr of tbl.querySelectorAll('tbody tr')){
    const tds=[...tr.querySelectorAll('td')].map(td=>td.innerText.trim());
    if(tds.length>8) out.push(JSON.stringify(tds));
  }
  return out.join('\\n');
})()"""

PAGER_INFO = """(()=>{
  const disp=document.body.innerText.match(/Displaying items (\\d+) - (\\d+) of (\\d+)/);
  const next=document.querySelector('.t-arrow-next');
  const dis=next&&(next.className||'').includes('t-state-disabled');
  return JSON.stringify({disp:disp?disp.slice(1):null,hasNext:!!next&&!dis});
})()"""


def parse_rows(raw, surname):
    rows = []
    for line in raw.splitlines():
        try:
            tds = json.loads(line)
        except Exception:
            continue
        # [checkbox, rowNum, empty, empty, #pages?, party_type, name, cross, doc#, date, doctype, booktype, map#, mapbkpg]
        if len(tds) < 11:
            continue
        party, name, cross = tds[5], tds[6], tds[7]
        docnum, date, doctype = tds[8], tds[9], tds[10]
        if not docnum or not re.match(r"\d{4}-\d+", docnum):
            continue
        # guard against stale grid from the previous query
        if not (name.upper().startswith(surname) or surname in cross.upper()):
            continue
        rows.append((docnum, date, doctype, party, name, cross, surname))
    return rows


def one_query(tab, surname, y0, y1, conn, pace):
    """Run one (surname, year-range) search; returns (groups, nrows)."""
    to = f"12/31/{y1}" if y1 < time.localtime().tm_year else time.strftime("%m/%d/%Y")
    tab.nav(f"{BASE}/search/SearchTypeName", 5)
    for _ in range(2):
        r = tab.ev(FILL % (surname, f"1/1/{y0}", to))
        if r == "set":
            break
        if r == "toc":
            time.sleep(4)
        else:
            tab.nav(f"{BASE}/search/SearchTypeName", 6)
    else:
        raise RuntimeError(f"form fill failed: {r}")
    tab.ev("document.getElementById('btnSearch').click()")
    time.sleep(6)
    # prename window?
    groups = 0
    for _ in range(3):
        pre = tab.ev("(()=>{const tv=document.getElementById('NameListTreeView'); return tv?tv.innerText.length:0;})()")
        if pre and pre > 0:
            groups = tab.ev("""(()=>{return document.querySelectorAll('#NameListTreeView input[name$=".Checked"]').length;})()""") or 0
            if groups > 475:
                return ("SPLIT", 0)
            r = tab.ev(PREENAME_DONE)
            if r == "nodone":
                return ("SPLIT", 0)
            time.sleep(6)
            break
        time.sleep(3)
    # results may still be loading
    total_rows = []
    for _ in range(8):
        raw = tab.ev(GRID_ROWS)
        if raw:
            break
        time.sleep(3)
    page = 0
    while True:
        raw = tab.ev(GRID_ROWS)
        rows = parse_rows(raw, surname)
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO parties (doc_num,record_date,doc_type,party_type,name,cross_party,query_surname) "
                "VALUES (?,?,?,?,?,?,?)", rows)
            total_rows += rows
        info = tab.ev(PAGER_INFO)
        try:
            info = json.loads(info)
        except Exception:
            info = {}
        page += 1
        if not info.get("hasNext") or page > 60:
            break
        tab.ev("""(()=>{const n=document.querySelector('.t-arrow-next'); if(n&&!n.className.includes('t-state-disabled')){n.click(); return 1;} return 0;})()""")
        time.sleep(4)
    conn.commit()
    time.sleep(pace + random.uniform(0, 1.5))
    return (groups, len(total_rows))


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
                    groups, nrows = one_query(tab, surname, y, y, conn, args.pace)
                    if groups == "SPLIT":
                        # surname exceeds 500-group cap for the year: half-year windows
                        groups, nrows = one_query_half(tab, surname, y, conn, args.pace)
                    status = "ok"
                    conn.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?,?,?)",
                                 (surname, y, y, status, groups, nrows))
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


def one_query_half(tab, surname, y, conn, pace):
    """Year split into halves for surnames exceeding the 500-group cap."""
    total = 0
    for mf, mt in [("1/1", "6/30"), ("7/1", "12/31")]:
        to = f"{mt}/{y}" if y < time.localtime().tm_year else (
            f"{mt}/{y}" if time.strftime("%m/%d/%Y") > f"{mt}/{y}" else time.strftime("%m/%d/%Y"))
        tab.nav(f"{BASE}/search/SearchTypeName", 5)
        for _ in range(2):
            r = tab.ev(FILL % (surname, f"{mf}/{y}", to))
            if r == "set":
                break
            if r == "toc":
                time.sleep(4)
        tab.ev("document.getElementById('btnSearch').click()")
        time.sleep(6)
        groups = 0
        for _ in range(3):
            pre = tab.ev("(()=>{const tv=document.getElementById('NameListTreeView'); return tv?tv.innerText.length:0;})()")
            if pre and pre > 0:
                groups = tab.ev("""(()=>{return document.querySelectorAll('#NameListTreeView input[name$=".Checked"]').length;})()""") or 0
                r = tab.ev(PREENAME_DONE)
                time.sleep(6)
                break
            time.sleep(3)
        rows_all = []
        page = 0
        while True:
            raw = tab.ev(GRID_ROWS)
            rows = parse_rows(raw, surname)
            if rows:
                conn.executemany(
                    "INSERT OR IGNORE INTO parties (doc_num,record_date,doc_type,party_type,name,cross_party,query_surname) "
                    "VALUES (?,?,?,?,?,?,?)", rows)
                rows_all += rows
            info = tab.ev(PAGER_INFO)
            try:
                info = json.loads(info)
            except Exception:
                info = {}
            page += 1
            if not info.get("hasNext") or page > 60:
                break
            tab.ev("""(()=>{const n=document.querySelector('.t-arrow-next'); if(n&&!n.className.includes('t-state-disabled')){n.click(); return 1;} return 0;})()""")
            time.sleep(4)
        conn.commit()
        total += len(rows_all)
        time.sleep(pace + random.uniform(0, 1.5))
    return (0, total)


if __name__ == "__main__":
    main()
