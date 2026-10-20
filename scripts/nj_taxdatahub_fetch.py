#!/usr/bin/env python3
"""taxdatahub.com (BRT Technologies) county hub fetcher via organic Chrome CDP.

Covers counties missing/partial on taxrecords-nj.com:
  ATLANTIC  622e0489f94ca0ce44f394e3/Atlantic   (absent there)
  SOMERSET  62322899fa390ea73997514b/Somerset   (Bridgewater only there)
  CAMDEN    60d088c3d3501df3b0e45ddb/camden-county (3 munis only there)

Recon 2026-10-20:
  - POST /search/<GUID>/SubmitSearchTerm {searchTerm, gRecaptchaToken}
  - server VALIDATES the captcha token AND its action: action must be
    "search" ("submit" -> "Possible BOT detected!")
  - RequestVerificationToken embedded in the hub page's inline JS
    (jqxhr.setRequestHeader('RequestVerificationToken', "CfDJ8...")
  - response Data.Documents = 40/page, Data.DocTotal = match count,
    paginate via searchTerm.From
  - all requests run as page-context fetch() in an organic Chrome tab
    (raw curl -> 400; dummy token -> 400; wrong action -> BOT flag)

Stores into the same nj_properties schema (subset of fields — the list
endpoint carries no mailing address/taxes). Resume-aware via
nj_fetch_log, same as nj_fetch.py.

Usage: python scripts/nj_taxdatahub_fetch.py [--counties ATLANTIC,...]
"""
import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time
import shutil

import websocket  # websocket-client

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

DB_PATH = os.path.join(ROOT, "data", "nj_properties.db")
PORT = 9224  # SDAT rig owns 9223, debug Chrome 9222

BROWSER = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

HUBS = {
    "ATLANTIC": "622e0489f94ca0ce44f394e3/Atlantic",
    "SOMERSET": "62322899fa390ea73997514b/Somerset",
    "CAMDEN": "60d088c3d3501df3b0e45ddb/camden-county",
}
SITEKEY = "6Le-rx8gAAAAAPVm6T9P2acvLFeInkggLGmxLblZ"

# injected once per hub page; returns {err, docs, docTotal} — everything
# stays in page context so cookies/origin/captcha all match the real app
JS_SEARCH = """
async (owner, frm) => {
  const html = document.documentElement.outerHTML;
  const m = html.match(/RequestVerificationToken', "([^"]+)"/);
  const tok = await grecaptcha.execute('%s', {action: 'search'});
  const st = {From: frm, MuniCodes: [], Phrase: '*', BLQs: [], ClassCodes: [],
    Class4Codes: [], SaleStartDate: null, SaleEndDate: null, OwnerName: owner,
    PropertyLocation: '', SalePriceFrom: null, SalePriceTo: null,
    BuildingSqFtFrom: null, BuildingSqFtTo: null, YearBuiltFrom: null,
    YearBuiltTo: null, IncludeAdditionalLots: false, Sort: 2};
  const r = await fetch('search/%s/SubmitSearchTerm', {method: 'POST',
    headers: {'Content-Type': 'application/json; charset=UTF-8',
              'X-Requested-With': 'XMLHttpRequest',
              'RequestVerificationToken': m[1]},
    body: JSON.stringify({searchTerm: st, gRecaptchaToken: tok})});
  const j = await r.json();
  if (j.IsError) return {err: j.StatusMsg || 'error'};
  const d = j.Data || {};
  const docs = (d.Documents || []).map(x => ({
    pin: x.CS_PaddedPin || '', owner: (x.OwnerName || '').replace(/<\\/?em>/g, ''),
    loc: x.PropertyLocation || '', muni: x.PropertyMuniLabel || '',
    block: (x.Block && x.Block.BlockTrimmed) || '', lot: (x.Lot && x.Lot.LotTrimmed) || '',
    cls: x.PropertyClassCode || '', sale_date: (x.SaleDate || '').slice(0, 10),
    sale_price: x.SalePrice == null ? null : x.SalePrice,
    yr: x.YearBuilt == null ? '' : String(x.YearBuilt),
    sqft: x.BuildingSqFt == null ? '' : String(x.BuildingSqFt)}));
  return {err: null, docs, total: d.DocTotal || 0};
}
"""


class CDP:
    """Minimal organic-Chrome CDP driver (SDAT rig pattern)."""

    def __init__(self, port=PORT):
        self.port = port
        self.proc = None
        self.profile = None
        self.ws = None
        self._mid = 0

    def start(self):
        subprocess.run(["pkill", "-f", f"remote-debugging-port={self.port}"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(1)
        # PERSISTENT profile — recaptcha v3 scores fresh profiles near zero
        # (every search after the first got "Possible BOT detected!" with a
        # throwaway dir; a warmed profile scores fine). Keep the dir across
        # runs so the score only improves.
        self.profile = os.path.join(tempfile.gettempdir(), "tdh_cdp_profile")
        os.makedirs(self.profile, exist_ok=True)
        self._keep_profile = True
        self.proc = subprocess.Popen([
            BROWSER, f"--user-data-dir={self.profile}", "--no-first-run",
            "--no-default-browser-check", f"--remote-debugging-port={self.port}",
            "--remote-allow-origins=*", "--window-size=1100,850", "about:blank",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import urllib.request
        for _ in range(30):
            try:
                with urllib.request.urlopen(
                        f"http://127.0.0.1:{self.port}/json/version", timeout=3) as r:
                    json.loads(r.read())
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("CDP port never came up")
        target = None
        import urllib.request
        for _ in range(20):
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/json/list", timeout=3) as r:
                for t in json.loads(r.read()):
                    if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                        target = t
                        break
            if target:
                break
            time.sleep(0.5)
        self.ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=120)
        self.cmd("Page.enable")
        self.cmd("Runtime.enable")
        return self

    def close(self):
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except Exception:
                self.proc.kill()
        if self.profile and not getattr(self, "_keep_profile", False):
            shutil.rmtree(self.profile, ignore_errors=True)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    def cmd(self, method, **params):
        self._mid += 1
        mid = self._mid
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method}: {msg['error']}")
                return msg.get("result", {})

    def js(self, expr, await_promise=False):
        r = self.cmd("Runtime.evaluate", expression=expr,
                     returnByValue=True, awaitPromise=await_promise)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"JS error: {r['exceptionDetails'].get('exception', {}).get('description', '')[:200]}")
        return r.get("result", {}).get("value")

    def navigate(self, url, wait=6):
        self.cmd("Page.navigate", url=url)
        time.sleep(wait)
        for _ in range(20):
            if self.js("document.readyState") == "complete":
                break
            time.sleep(0.5)
        time.sleep(2)  # let grecaptcha load

    def warm(self):
        """recaptcha v3 scores a brand-new profile poorly — give the
        persistent profile some organic history on first use."""
        self.navigate("https://www.google.com", wait=3)
        self.js("document.querySelector('input[name=q]') && document.querySelector('input[name=q]').focus()")
        time.sleep(4)


def surnames():
    sys.path.insert(0, os.path.join(ROOT, "scripts"))
    from nj_fetch import surnames as _s
    return _s()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--counties", default=",".join(HUBS))
    args = ap.parse_args()

    from src.community_pipeline import RaceEthnicityPredictor
    predictor = RaceEthnicityPredictor()
    predictor.use_ethnicolr = False

    con = sqlite3.connect(DB_PATH, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE IF NOT EXISTS nj_properties (
        county TEXT NOT NULL, district TEXT, block TEXT, lot TEXT, qual TEXT,
        property_id TEXT PRIMARY KEY, owner_name TEXT, mailing_address TEXT,
        city_state_zip TEXT, property_location TEXT, prop_class TEXT,
        land_assmnt INTEGER, building_assmnt INTEGER, total_assmnt INTEGER,
        taxes REAL, sale_date TEXT, sale_price INTEGER,
        year_built TEXT, sq_ft TEXT, acreage TEXT,
        is_hindu INTEGER DEFAULT 0, is_indian INTEGER DEFAULT 0, category TEXT,
        first_seen TEXT, last_seen TEXT,
        latitude REAL, longitude REAL, geo_method TEXT)""")
    con.execute("""CREATE TABLE IF NOT EXISTS nj_fetch_log (
        county TEXT, query TEXT, count INTEGER, rows_stored INTEGER,
        fetched_at TEXT, PRIMARY KEY (county, query))""")
    con.commit()

    q_list = surnames()
    print(f"{len(q_list)} surnames x {len(args.counties.split(','))} hubs")

    grand = 0
    with CDP() as b:
        b.warm()
        for county in args.counties.split(","):
            county = county.strip().upper()
            hub = HUBS[county]
            b.navigate(f"https://www.taxdatahub.com/{hub}")
            # sanity: grecaptcha object present
            for _ in range(10):
                if b.js("typeof grecaptcha !== 'undefined' && typeof grecaptcha.execute === 'function'"):
                    break
                time.sleep(1)
            else:
                print(f"{county}: grecaptcha never ready, skipping")
                continue
            fn = JS_SEARCH % (SITEKEY, hub.split("/")[0])

            for q in q_list:
                # log key is namespaced ("<COUNTY>/tdh") — the plain county
                # keys belong to the taxrecords-nj walk (CAMDEN/SOMERSET are
                # logged full there but only partially covered)
                logkey = f"{county}/tdh"
                if con.execute("SELECT 1 FROM nj_fetch_log WHERE county=? AND query=?",
                               (logkey, q)).fetchone():
                    continue
                stored = 0
                frm = 0
                total = 0
                bot_retries = 0
                while True:
                    raw = b.js(f"({fn})({json.dumps(q)}, {frm})", await_promise=True)
                    if not raw or raw.get("err"):
                        # BOT flag / transient — cool down and retry a few times
                        msg = (raw or {}).get("err") or "no-response"
                        if bot_retries < 4:
                            bot_retries += 1
                            print(f"  {county} {q}: '{msg}' retry {bot_retries}")
                            time.sleep(20 * bot_retries)
                            continue
                        print(f"  {county} {q}: '{msg}' GIVING UP")
                        break
                    docs = raw["docs"]
                    total = raw["total"]
                    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
                    for d in docs:
                        pid = d["pin"] or " | ".join(
                            filter(None, [d["muni"], d["block"], d["lot"]]))
                        if not pid.strip(" |"):
                            continue
                        # skip if the taxrecords-nj load already has this parcel
                        # (SOMERSET/CAMDEN partial coverage overlaps)
                        dup = con.execute(
                            "SELECT 1 FROM nj_properties WHERE county=? AND "
                            "UPPER(district)=? AND TRIM(block)=? AND TRIM(lot)=?",
                            (county, d["muni"].upper(),
                             (d["block"] or "").strip(), (d["lot"] or "").strip())
                        ).fetchone()
                        if dup:
                            continue
                        res = predictor.predict_race(d["owner"])
                        cur = con.execute(
                            "INSERT OR IGNORE INTO nj_properties "
                            "(county, district, block, lot, qual, property_id, owner_name,"
                            " property_location, prop_class, sale_date, sale_price,"
                            " year_built, sq_ft, is_hindu, is_indian, category,"
                            " first_seen, last_seen)"
                            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (county, d["muni"].upper(), d["block"], d["lot"], "",
                             f"{county}|{pid}", d["owner"], d["loc"], d["cls"],
                             d["sale_date"], d["sale_price"], d["yr"], d["sqft"],
                             1 if res.get("is_hindu") else 0,
                             1 if res.get("is_indian") else 0,
                             res.get("sub_category") or "", now, now))
                        if cur.rowcount:
                            stored += 1
                    con.commit()
                    frm += len(docs)
                    if frm >= total or not docs:
                        break
                    time.sleep(0.8)
                grand += stored
                con.execute("INSERT OR REPLACE INTO nj_fetch_log VALUES (?,?,?,?,datetime('now'))",
                            (f"{county}/tdh", q, total, stored))
                con.commit()
                if stored:
                    print(f"  {county} {q}: {total} found, +{stored} new", flush=True)
                time.sleep(1.5)

    tot = con.execute("SELECT COUNT(*), SUM(is_hindu) FROM nj_properties").fetchone()
    print(f"\nTOTAL {tot[0]:,} rows, {tot[1] or 0:,} hindu")
    con.close()


if __name__ == "__main__":
    main()
