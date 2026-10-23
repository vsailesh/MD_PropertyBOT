#!/usr/bin/env/python3
#!/usr/bin/env python3
"""Placer County CA recorder (countyfusion4.kofiletech.us, Kofile
CountyFusion) grantor/grantee deed-party fetcher via CDP organic Chrome
(port 9238, profile /tmp/claude-501/placer_profile).

Deep-frameset app: main.jsp -> bodyframe -> searchMain.do ->
searchCriteria.do -> dynCriteria.do (easyui form); results in
docs_SearchResultList.jsp frame. Sequence per session:
  loginDisplay -> doGuestLogin() -> disclaimer form submit ->
  welcome datagrid row 'Search Public Records' click ->
  dynCriteria $('#allNames'/'#FROMDATE'/'#TODATE').textbox('setValue') ->
  navbar.parent.executeSearchCommand('search')
Results text blocks: <docnum 2024-0055738-00> + / NameType (1|2) / NAME /
OtherType / OTHER NAME / DOC TYPE / date. NameType 1 = grantor-ish party,
2 = other party (kept as PARTY_1/PARTY_2). Pager = navbar resultNav next.

DB: data/ca_placer_deeds.db (parties + fetch_log, resume-safe)
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
DB = ROOT / "data" / "ca_placer_deeds.db"
BASE = "https://countyfusion4.kofiletech.us/countyweb"
CDP = "http://localhost:9238"

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
        tab = next((t for t in tabs if "kofiletech" in (t.get("url") or "")), None)
        if not tab:
            req = urllib.request.Request(f"{CDP}/json/new?{BASE}/loginDisplay.action?countyname=Placer", method="PUT")
            tab = json.load(urllib.request.urlopen(req))
            time.sleep(8)
        self.ws = websocket.create_connection(
            tab["webSocketDebuggerUrl"], timeout=150, origin="http://localhost:9238")
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
                                          "returnByValue": True, "awaitPromise": True})
        return r.get("result", {}).get("result", {}).get("value")

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


def find_frame(tab, key):
    return tab.ev(f"""(()=>{{
      const rec=(w)=>{{let out=[]; for(let i=0;i<w.frames.length;i++){{const f=w.frames[i];
        let u=''; try{{u=f.location.href}}catch(e){{}}
        if(u.includes('{key}')) out.push(f); out=out.concat(rec(f));}} return out;}};
      return rec(window)[0];}})""")


def in_frame(tab, key, js):
    """Run js (with $ = frame var) inside EVERY frame matching key;
    returns the first non-null result (falls back to first frame)."""
    return tab.ev(f"""(()=>{{
      const rec=(w)=>{{let out=[]; for(let i=0;i<w.frames.length;i++){{const f=w.frames[i];
        let u=''; try{{u=f.location.href}}catch(e){{}}
        if(u.includes('{key}')) out.push(f); out=out.concat(rec(f));}} return out;}};
      const hits=rec(window); if(!hits.length) return null;
      let first=null;
      for (const fr of hits) {{
        const d=fr.document; const $=fr.jQuery||window.jQuery;
        try {{ const v={js}; if(first===null) first=v; if(v) return v; }}
        catch(e) {{ if(first===null) first='ERR '+e.message; }}
      }}
      return first;
    }})()""")


def ensure_session(tab):
    """Drive guest login -> disclaimer -> search page until dynCriteria exists."""
    for _ in range(3):
        if in_frame(tab, "dynCriteria", "1"):
            return True
        # login page?
        if tab.ev("(document.querySelector('input[onclick*=doGuestLogin]')?1:0)"):
            tab.ev("doGuestLogin()")
            time.sleep(8)
        # disclaimer in bodyframe?
        r = tab.ev("""(()=>{const f=document.getElementById('bodyframe'); if(!f) return 0;
          const d=f.contentDocument; if(!d.body) return 0;
          const h=d.querySelector('input[type=hidden][value=Accept]');
          if(h&&d.body.innerText.includes('Disclaimer')){ h.closest('form').submit(); return 2;}
          const row=[...d.querySelectorAll('tr.datagrid-row')].find(r=>/Search Public Records/i.test(r.textContent));
          if(row){ row.click(); return 3;}
          return 1;})()""")
        time.sleep(9)
        if r == 3:
            time.sleep(4)
    return bool(in_frame(tab, "dynCriteria", "1"))


def results_text(tab):
    """Longest innerText among docs_SearchResultList frames (header-only
    frames shadow the grid in DFS order)."""
    return tab.ev("""(()=>{const rec=(w)=>{let out=[];
      for(let i=0;i<w.frames.length;i++){const f=w.frames[i];
        let u=''; try{u=f.location.href}catch(e){}
        if(u.includes('docs_SearchResultList')) out.push(f);
        out=out.concat(rec(f));} return out;};
      const hits=rec(window); if(!hits.length) return null;
      let best=''; for (const fr of hits){ try{ const t=fr.document.body.innerText;
        if(t&&t.length>best.length) best=t; }catch(e){} }
      return best||null;})()""")


DOC_RE = re.compile(r"(\d{4}-\d{7}-\d{2})")
NAME_RE = re.compile(r"^[A-Z][A-Z &/,'.-]*$")


def parse_body(body, surname):
    """Result-list text -> party rows (name first token == surname)."""
    rows = []
    hits = list(DOC_RE.finditer(body))
    for i, m in enumerate(hits):
        doc = m.group(1)
        seg = body[m.end(): hits[i + 1].start() if i + 1 < len(hits) else len(body)]
        dt = re.search(r"\n\s*(DEED[^/\n]*|RECONVEYANCE|SUBSTITUTION[^/\n]*|TRUST DEED|"
                       r"ASSIGNMENT[^/\n]*|LIEN[^/\n]*|UCC[^/\n]*|RELEASE[^/\n]*|"
                       r"MODIFICATION[^/\n]*|NOTICE[^/\n]*|EASEMENT[^/\n]*|"
                       r"AFFIDAVIT[^/\n]*|SATISFACTION[^/\n]*|ABANDONMENT[^/\n]*)\s*\n", seg)
        doctype = dt.group(1).strip() if dt else ""
        dm = re.search(r"(\d{2}/\d{2}/\d{4})", seg)
        date = dm.group(1) if dm else ""
        # lines after the two type digits: NAME then optional other name
        lines = [l.strip() for l in seg.split("\n") if l.strip()]
        # find name-type markers: a lone 1 or 2 line
        for j, l in enumerate(lines):
            if l in ("1", "2") and j + 1 < len(lines):
                name = lines[j + 1].rstrip("+").strip()
                if NAME_RE.match(name) and name.split()[0] == surname:
                    rows.append((doc, date, doctype,
                                 "PARTY_1" if l == "1" else "PARTY_2",
                                 name, surname))
    return rows


def one_query(tab, surname, y, conn, pace):
    if not ensure_session(tab):
        raise RuntimeError("session setup failed")
    r = in_frame(tab, "dynCriteria", """(()=>{$('#allNames').textbox('setValue','%s');
      $('#FROMDATE').textbox('setValue','01/01/%d');
      $('#TODATE').textbox('setValue','12/31/%d');
      return 'name='+$('#allNames').textbox('getValue');})()""" % (surname, y, y))
    if not r or str(r).startswith("ERR"):
        raise RuntimeError(f"setValues failed: {r}")
    # fire search from navbar frame's parent
    tab.ev("""(()=>{const rec=(w)=>{let out=[]; for(let i=0;i<w.frames.length;i++){const f=w.frames[i];
      let u=''; try{u=f.location.href}catch(e){} if(u.includes('navbar.do')) out.push(f); out=out.concat(rec(f));} return out;};
      const nb=rec(window)[0]; if(!nb) return 'nonav'; nb.parent.executeSearchCommand('search'); return 1;})()""")
    # wait for result list frame
    body = ""
    for _ in range(40):
        time.sleep(2.5)
        body = results_text(tab) or ""
        if body and ("Document #" in body or "No " in body[:200] or "exceeds" in body):
            break
    total_rows = parse_body(body, surname)
    if "exceeds maximum" in body:
        raise RuntimeError("exceeds max — split range")
    # pager: navbar resultNav 'next'
    guard = 0
    while guard < 50:
        guard += 1
        nxt = tab.ev("""(()=>{const rec=(w)=>{let out=[]; for(let i=0;i<w.frames.length;i++){const f=w.frames[i];
          let u=''; try{u=f.location.href}catch(e){} if(u.includes('navbar.do')&&u.includes('resultNav')) out.push(f); out=out.concat(rec(f));} return out;};
          const nb=rec(window)[0]; if(!nb) return null; const d=nb.document;
          const a=[...d.querySelectorAll('a')].find(a=>a.offsetParent&&/next/i.test((a.innerText||'')+(a.className||''))&&!/prev/i.test(a.innerText||''));
          if(!a) return null; a.click(); return 1;})()""")
        if not nxt:
            break
        newbody = ""
        for _ in range(12):
            time.sleep(2.5)
            newbody = results_text(tab) or ""
            if newbody and newbody != body:
                break
        if not newbody or newbody == body:
            break
        body = newbody
        total_rows += parse_body(body, surname)
    if total_rows:
        conn.executemany(
            "INSERT OR IGNORE INTO parties (doc_num,record_date,doc_type,party_type,name,query_surname) "
            "VALUES (?,?,?,?,?,?)", total_rows)
    n_docs = len({r[0] for r in total_rows})
    conn.execute("INSERT OR REPLACE INTO fetch_log VALUES (?,?,?,?,?,?)",
                 (surname, y, y, "ok", n_docs, len(total_rows)))
    conn.commit()
    time.sleep(pace + random.uniform(0, 1.0))
    return (n_docs, len(total_rows))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", default="2015-2026")
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
                    total, nrows = one_query(tab, surname, y, conn, args.pace)
                    print(f"{surname} {y}: {total} docs {nrows} rows", flush=True)
                    break
                except Exception as e:
                    print(f"ERR {surname} {y} attempt {attempt}: {e}", flush=True)
                    time.sleep(3)
                    tab.reconnect()
                    tab.nav(f"{BASE}/loginDisplay.action?countyname=Placer", 8)
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
