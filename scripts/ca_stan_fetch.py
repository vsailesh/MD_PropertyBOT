#!/usr/bin/env python3
"""Stanislaus County CA recorder (crweb.stancounty.com/RecorderWorksInternet)
grantor/grantee deed-party fetcher via CDP organic Chrome
(port 9232, profile /tmp/claude-501/stan_profile).

ASP.NET WebForms "RecorderWorks" app, index 1850-present, no captcha.
  - Name tab: #.._nameForSearch (LAST [space] FIRST), partytype select,
    allowPartial (default checked), FromDate/ToDate MM/DD/YYYY
  - search fires DIV btnSearch onclick: search.OnSearch('.searchByName','&SetCriteria=true')
  - results: tr.searchResultRow / page (20 rows), cells:
      docnum = row.cells[1], .GrtContainer, .GrteeContainer,
      .DocTypeContainer (may repeat per instrument), .recDate
  - pager: td.pagingCellNumber onclick search.OnPage('N','.booking');
    'Next' td.pagingCellNext; total 'NN Result(s)'
  - PATEL partial-match pulls PATELCO CREDIT UNION noise -> keep only
    names whose first token == surname

DB: data/ca_stan_deeds.db (parties + fetch_log, resume-safe)
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
DB = ROOT / "data" / "ca_stan_deeds.db"
BASE = "https://crweb.stancounty.com/RecorderWorksInternet?ln=en"
CDP = "http://localhost:9232"
P = "MainContent_MainMenu1_SearchByName1_"

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
        tab = next((t for t in tabs if "stancounty" in (t.get("url") or "")), None)
        if not tab:
            req = urllib.request.Request(f"{CDP}/json/new?{BASE}", method="PUT")
            tab = json.load(urllib.request.urlopen(req))
            time.sleep(8)
        self.ws = websocket.create_connection(
            tab["webSocketDebuggerUrl"], timeout=120, origin="http://localhost:9232")
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
        try:
            return json.loads(v) if isinstance(v, str) else v
        except Exception:
            return None

    def nav(self, url, wait=8):
        self.cmd("Page.navigate", {"url": url})
        time.sleep(wait)

    def reconnect(self):
        try:
            self.ws.close()
        except Exception:
            pass
        time.sleep(2)
        self.__init__()


ROWS_JS = """(()=>{
  const rows=[...document.querySelectorAll('tr.searchResultRow')];
  return JSON.stringify(rows.map(row=>({
    doc:(row.cells[1]?row.cells[1].innerText:'').trim(),
    grt:(row.querySelector('.GrtContainer')?row.querySelector('.GrtContainer').innerText:'').trim(),
    grtee:(row.querySelector('.GrteeContainer')?row.querySelector('.GrteeContainer').innerText:'').trim(),
    dtype:[...new Set([...row.querySelectorAll('.DocTypeContainer')].map(d=>d.innerText.trim()))].join(' | '),
    date:(row.querySelector('.recDate')?row.querySelector('.recDate').innerText:'').trim()
  })));
})()"""

TOTAL_RE = re.compile(r"(\d[\d,]*)\s*Result", re.I)


def page_count(tab):
    return tab.ev(
        "document.querySelectorAll('.pagingCellNumber:not(.pagingCellDisable)').length") or 0


def cur_page(tab):
    return tab.ev("""(()=>{const c=document.querySelector('.pagingCellCurrent');
      return c? parseInt(c.innerText)||1: 1;})()""")


def names(txt):
    out = []
    for line in (txt or "").split("\n"):
        t = line.strip()
        if len(t) >= 3 and re.match(r"^[A-Z][A-Z &/,'.-]*$", t):
            out.append(t)
    return out


def parse_rows(raw, surname):
    """Keep names whose first token equals the surname (kills PATELCO etc)."""
    rows = []
    for r in raw or []:
        doc = r.get("doc", "")
        if not re.match(r"\d{4}-\d{5,8}$", doc):
            continue
        date = r.get("date", "")
        dt = r.get("dtype", "")
        for n in names(r.get("grt", "")):
            if n.split()[0] == surname:
                rows.append((doc, date, dt, "GRANTOR", n, surname))
        for n in names(r.get("grtee", "")):
            if n.split()[0] == surname:
                rows.append((doc, date, dt, "GRANTEE", n, surname))
    return rows


def one_query(tab, surname, y, conn, pace):
    tab.nav(BASE, 8)
    # Name tab renders async — poll for the field
    ok = None
    for _ in range(12):
        ok = tab.ev(f"""(()=>{{const n=document.getElementById('{P}nameForSearch'); return n?1:0;}})()""")
        if ok:
            break
        time.sleep(1.5)
    if not ok:
        raise RuntimeError("no name field (Name tab)")
    tab.ev(f"""(()=>{{
      document.getElementById('{P}nameForSearch').value='{surname}';
      document.getElementById('{P}FromDate').value='01/01/{y}';
      document.getElementById('{P}ToDate').value='12/31/{y}';
      const pt=document.getElementById('{P}partytype'); if(pt) pt.value='0';
      return 1;}})()""")
    time.sleep(0.8)
    tab.ev("search.OnSearch('.searchByName','&SetCriteria=true')")
    # results (page fetch may take a while — poll 60s)
    body = ""
    raw = None
    for _ in range(30):
        time.sleep(2)
        body = tab.ev("document.body.innerText") or ""
        if "Result(s)" in body or "No records" in body.lower() or "no result" in body.lower():
            raw = tab.evj(ROWS_JS)
            if raw:
                break
    if raw is None:
        raw = tab.evj(ROWS_JS) or []
    tm = TOTAL_RE.search(body)
    total = int(tm.group(1).replace(",", "")) if tm else 0
    all_rows = parse_rows(raw, surname)
    # pager
    npages = page_count(tab)
    seen_docs = {r["doc"] for r in raw or []}
    page = cur_page(tab)
    guard = 0
    while page < npages and page < 50 and guard < 60:
        guard += 1
        tab.ev(f"search.OnPage('{page + 1}','.booking')")
        newraw = None
        for _ in range(15):
            time.sleep(2)
            newraw = tab.evj(ROWS_JS)
            if newraw and {r["doc"] for r in newraw} - seen_docs:
                break
        if not newraw or cur_page(tab) == page:
            break
        seen_docs |= {r["doc"] for r in newraw}
        all_rows += parse_rows(newraw, surname)
        page = cur_page(tab)
    if all_rows:
        conn.executemany(
            "INSERT OR IGNORE INTO parties (doc_num,record_date,doc_type,party_type,name,query_surname) "
            "VALUES (?,?,?,?,?,?)", all_rows)
    conn.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?,?,?)",
                 (surname, y, y, "ok", total, len(all_rows)))
    conn.commit()
    time.sleep(pace + random.uniform(0, 1.2))
    return (total, len(all_rows))


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
