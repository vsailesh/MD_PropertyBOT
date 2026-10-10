#!/usr/bin/env python3
"""Browser-driven SDAT scraper over CDP (Oct 2026).

SDAT's Cloudflare WAF now 403s every automated POST path — plain requests,
curl_cffi with Chrome TLS, even Selenium (webdriver detected). The results
UI was also redesigned into a paginated grid, so the old detail-page parser
is dead. The only working path is an organic Chrome driven over the
DevTools protocol: the challenge clears silently in a flagless browser and
the ASP.NET wizard + grid both work.

Consumes the same SQLite queue protocol as robust_bulk_search.py
(pending -> in_progress -> completed, replace mode, crash-proof resume),
so the watchdog / rotation machinery is unchanged.

Usage:
  python scripts/sdat_browser_scraper.py dump --street MAIN --county "ANNE ARUNDEL"
      One search, dump the results-grid DOM so the parser can be verified.
  python scripts/sdat_browser_scraper.py run [--limit N] [--replace]
      Drain the oldest batch with pending streets (FIFO, like the watchdog).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

PORT = 9223
SDAT = "https://sdat.dat.maryland.gov/RealProperty/Pages/default.aspx"
# Chrome lives on the boot volume; Brave is on the external disk and
# vanishes with it (same candidate order as sdat_clearance.py).
_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Volumes/HulkBuster/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]
BROWSER = next((p for p in _CANDIDATES if os.path.exists(p)), _CANDIDATES[0])

# ASP.NET control IDs from the real property search wizard (stable since
# the redesign — verified via CDP on 2026-10-09).
P = "cphMainContentArea_ucSearchType_wzrdRealPropertySearch_"
ID_COUNTY = P + "ucSearchType_ddlCounty"
ID_SEARCHTYPE = P + "ucSearchType_ddlSearchType"
ID_CONTINUE = P + "StartNavigationTemplateContainerID_btnContinue"
ID_STREET = P + "ucEnterData_txtStreetName"
ID_NEXT = P + "StepNavigationTemplateContainerID_btnStepNextButton"

COUNTY_MAP = {
    'ALLEGANY': '01', 'ANNE ARUNDEL': '02', 'BALTIMORE CITY': '03',
    'BALTIMORE COUNTY': '04', 'CALVERT': '05', 'CAROLINE': '06',
    'CARROLL': '07', 'CECIL': '08', 'CHARLES': '09', 'DORCHESTER': '10',
    'FREDERICK': '11', 'GARRETT': '12', 'HARFORD': '13', 'HOWARD': '14',
    'KENT': '15', 'MONTGOMERY': '16', "PRINCE GEORGE'S": '17',
    'QUEEN ANNE\'S': '18', 'ST. MARY\'S': '19', 'SOMERSET': '20',
    'TALBOT': '21', 'WASHINGTON': '22', 'WICOMICO': '23', 'WORCESTER': '24',
}


def name_variants(street: str):
    """Search-name variations, proven from the requests-engine era — SDAT
    needs the registered spelling (ST vs SAINT, MC spacing, BALTO/NATL
    abbreviations), and zero-result re-scrapes would just re-zero without
    trying them. Base form first; only walked when the base finds nothing.
    """
    base = " ".join(str(street).upper().split())
    if not base or base == "UNKNOWN":
        return []
    out = [base]
    if " " in base:
        out.append(base.replace(" ", ""))
    elif base.startswith("MC") and len(base) > 2:
        out.append(base.replace("MC", "MC ", 1))
    if base.startswith("ST "):
        out.append(base.replace("ST ", "SAINT ", 1))
    elif base.startswith("SAINT "):
        out.append(base.replace("SAINT ", "ST ", 1))
    if "BALTIMORE" in base:
        out.append(base.replace("BALTIMORE", "BALTO"))
    if "NATIONAL" in base:
        out.append(base.replace("NATIONAL", "NATL"))
    return list(dict.fromkeys(v for v in out if v and v != "UNKNOWN"))


def county_id(county: str) -> str:
    """Normalize a county name to its SDAT dropdown value."""
    c = (county or "").upper().strip()
    is_city = c.endswith(" CITY")
    for suffix in (" COUNTY", " CITY"):
        if c.endswith(suffix):
            c = c[: -len(suffix)].strip()
            break
    if c in COUNTY_MAP:
        return COUNTY_MAP[c]
    if COUNTY_MAP.get(c + (" CITY" if is_city else " COUNTY")):
        return COUNTY_MAP[c + (" CITY" if is_city else " COUNTY")]
    for k, v in COUNTY_MAP.items():
        if k.startswith(c) and not (is_city ^ k.endswith(" CITY")):
            return v
    return None


class BrowserBlockedError(Exception):
    """Cloudflare challenge did not clear — stop, retry after cooldown."""


class CDPBrowser:
    """Organic Chrome over the DevTools protocol (no webdriver flags)."""

    def __init__(self, port=PORT):
        self.port = port
        self.proc = None
        self.profile = None
        self.ws = None
        self._mid = 0

    # -- lifecycle ---------------------------------------------------
    def start(self):
        import websocket  # websocket-client

        # Kill any orphaned scraper browser from a SIGKILLed previous pass
        # — an orphan holding port 9223 makes every later launch fail
        # ("port never came up") and the pass backs off forever. The
        # pattern matches only browsers with our debug flag.
        try:
            subprocess.run(["pkill", "-f", f"remote-debugging-port={self.port}"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1)
        except Exception:
            pass

        self.profile = tempfile.mkdtemp(prefix="sdat_cdp_")
        args = [
            BROWSER,
            f"--user-data-dir={self.profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-brave-update",
            f"--remote-debugging-port={self.port}",
            "--remote-allow-origins=*",  # handshake 403s without this
            "--window-size=1100,850",
            "about:blank",
        ]
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        if not self._wait_port(15):
            raise RuntimeError("CDP port never came up")

        target = None
        for _ in range(20):
            for t in self._http("/json/list"):
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    target = t
                    break
            if target:
                break
            time.sleep(0.5)
        if not target:
            raise RuntimeError("no page target")

        self.ws = websocket.create_connection(target["webSocketDebuggerUrl"],
                                              timeout=60)
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
        if self.profile:
            shutil.rmtree(self.profile, ignore_errors=True)

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()

    # -- plumbing ----------------------------------------------------
    def _http(self, path):
        import urllib.request
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}{path}",
                                    timeout=5) as r:
            return json.loads(r.read().decode())

    def _wait_port(self, timeout=15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                self._http("/json/version")
                return True
            except Exception:
                time.sleep(0.5)
        return False

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
        """Evaluate JS in the page, return the JSON value (or None)."""
        r = self.cmd("Runtime.evaluate",
                     expression=expr,
                     returnByValue=True,
                     awaitPromise=await_promise)
        if r.get("exceptionDetails"):
            raise RuntimeError(f"JS error: {r['exceptionDetails'].get('text')}")
        return (r.get("result") or {}).get("value")

    def title(self):
        try:
            return self.js("document.title") or ""
        except Exception:
            return ""

    def navigate(self, url):
        self.cmd("Page.navigate", url=url)

    # -- SDAT flow ---------------------------------------------------
    def goto_sdat(self, timeout=90):
        """Navigate to SDAT and wait out any Cloudflare interstitial."""
        self.navigate(SDAT)
        deadline = time.time() + timeout
        while time.time() < deadline:
            time.sleep(3)
            t = self.title()
            if t and "just a moment" not in t.lower() and t != "about:blank":
                return t
        raise BrowserBlockedError(f"challenge never cleared (title={self.title()!r})")

    def _wait_js(self, expr, timeout=60, poll=1.0):
        """Poll until JS expr is truthy; return its value. Tolerates the
        bare 'Uncaught' errors CDP reports mid-navigation (the execution
        context is destroyed between pages)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                v = self.js(expr)
            except Exception:
                v = None
            if v:
                return v
            time.sleep(poll)
        return None

    def search_street(self, street: str, cid: str, timeout=90) -> bool:
        """Run the wizard for one street. True if a results grid rendered.

        Always starts with a fresh navigation — after a previous search
        the wizard sits on the results page, where the county select (and
        the whole step-1 form) doesn't exist. A plain GET resets to
        step 1; the challenge (if any) clears before the form renders,
        so "form never appears" doubles as a block signal.
        """
        self.navigate(SDAT)
        if not self._wait_js(f"document.getElementById('{ID_COUNTY}') ? 1 : 0",
                             timeout=timeout, poll=2):
            raise BrowserBlockedError("wizard form never appeared after navigation")
        # 1. Wizard step 1: county + search type, then Continue.
        #    The county select ONLY advances the wizard when the change
        #    event bubbles — plain .value/.click() and __doPostBack alone
        #    all leave the form stuck.
        self.js(f"""
            (() => {{
                const c = document.getElementById('{ID_COUNTY}');
                if (!c) return 'no county select';
                c.value = '{cid}';
                c.dispatchEvent(new Event('change', {{bubbles: true}}));
                const t = document.getElementById('{ID_SEARCHTYPE}');
                if (t) t.value = '01';   // street address search
                return 1;
            }})()
        """)
        time.sleep(1.5)
        self.js(f"""
            (() => {{
                const b = document.getElementById('{ID_CONTINUE}');
                if (!b) return 0;
                b.click(); return 1;
            }})()
        """)

        # 2. Wait for the street input (step 2), then submit.
        if not self._wait_js(f"document.getElementById('{ID_STREET}') ? 1 : 0",
                             timeout=45):
            return False
        # Street names from the queue are already SDAT-cleaned; strip to
        # the bare name the wizard expects.
        name = " ".join(street.upper().split())
        self.js(f"""
            (() => {{
                const s = document.getElementById('{ID_STREET}');
                if (!s) return 0;
                s.value = {json.dumps(name)};
                const n = document.getElementById('{ID_NEXT}');
                if (!n) return 0;
                n.click(); return 1;
            }})()
        """)

        # 3. Wait for the results grid (or a no-results page). The
        #    no-records page ALSO contains "Search Result for" text, so
        #    check it first — order matters.
        deadline = time.time() + timeout
        while time.time() < deadline:
            t = self.title()
            if "just a moment" in t.lower():
                raise BrowserBlockedError("challenge re-appeared mid-search")
            try:
                txt = (self.js("document.body ? document.body.innerText.slice(0, 4000) : ''") or "").lower()
            except Exception:
                txt = ""  # mid-navigation — execution context not ready

            if "no records that match" in txt:
                return False
            if "search result for" in txt:
                return True
            time.sleep(2)
        return False

    # -- grid reading -------------------------------------------------
    # The results grid lives INSIDE the wizard's wrapper table (whose id
    # ends wzrdRealPropertySearch) — querySelectorAll('tr') on the wrapper
    # returns a single mega-row while the inner tables are still rendering.
    # Target the inner grid by id (pager/map span ids revealed it), with a
    # find-by-header fallback.
    # Pager anatomy (verified 2026-10-09): clickable pages are <a> tags
    # with bare numeric text; the CURRENT page is a <span> with numeric
    # text and an empty id (grid map/parcel cells are also numeric spans
    # but carry long ASP.NET ids — that's the discriminator).
    GRID_JS = """
        (() => {
            let grid = document.getElementById(
                'cphMainContentArea_ucSearchType_wzrdRealPropertySearch_ucSearchResult_gv_SearchResult');
            if (!grid) {
                grid = [...document.querySelectorAll('table')].find(t =>
                    /\\bName\\b/.test(t.innerText) && /\\bParcel\\b/.test(t.innerText));
            }
            if (!grid) return null;
            const rows = [...grid.querySelectorAll('tr')].map(tr =>
                [...tr.querySelectorAll('th,td')].map(c => c.innerText.trim()));
            const num = s => /^\\s*\\d+\\s*$/.test(s || '');
            const pages = [...document.querySelectorAll('a')]
                .filter(el => num(el.innerText))
                .map(el => ({text: el.innerText.trim(), clickable: true}));
            const current = [...document.querySelectorAll('span')]
                .filter(el => num(el.innerText) && !el.id)
                .map(el => ({text: el.innerText.trim(), clickable: false}));
            return {id: grid.id, rows, pager: current.concat(pages)};
        })()
    """

    @staticmethod
    def grid_parsed_ok(grid) -> bool:
        """True when the grid has a header row AND at least one row under
        it — the state parse_grid can actually produce records from."""
        rows = (grid or {}).get('rows') or []
        for i, r in enumerate(rows):
            labels = [c.lower().strip() for c in r]
            if 'name' in labels and 'parcel' in labels:
                return len(rows) > i + 1
        return False

    def read_grid(self):
        """Return {id, rows, pager} for the results table, or None."""
        return self.js(self.GRID_JS)

    def click_page(self, n) -> bool:
        """Click the pager <a> for page n. False if not present."""
        return bool(self.js(f"""
            (() => {{
                const el = [...document.querySelectorAll('a')]
                    .find(el => (el.innerText || '').trim() === String({n}));
                if (!el) return 0;
                el.click(); return 1;
            }})()
        """))

    def grid_ready(self) -> bool:
        txt = self.js("document.body ? document.body.innerText.slice(0, 4000) : ''") or ""
        return "Search Result for" in txt


def dump(street, county):
    """One search; print the grid DOM structure for parser verification."""
    cid = county_id(county)
    if not cid:
        print(f"county not mapped: {county}")
        return 1
    print(f"browser: {BROWSER}")
    with CDPBrowser() as b:
        print(f"landed: {b.goto_sdat()!r}")
        ok = b.search_street(street, cid)
        print(f"search returned grid: {ok}")
        if not ok:
            txt = b.js("document.body.innerText.slice(0, 1500)") or ""
            print("--- body head ---")
            print(txt)
            return 2
        time.sleep(2)
        grid = b.read_grid()
        if not grid:
            print("grid table not found — body head:")
            print(b.js("document.body.innerText.slice(0, 1500)") or "")
            return 3
        print(f"grid id: {grid['id']!r}  rows: {len(grid['rows'])}")
        for r in grid['rows'][:8]:
            print("  ", r)
        print(f"pager: {json.dumps(grid['pager'][:15])}")
        return 0


def run(limit=None, replace=False, input_path=None, force=False, name=None):
    """Create a job from Excel (if -i) and/or drain the oldest pending
    batch, FIFO — same queue semantics as robust_bulk_search.py so the
    watchdog/rotation machinery is unchanged."""
    import signal

    from src.robust_database import PropertyDatabase, SearchJobManager
    from src.community_pipeline import RaceEthnicityPredictor

    # SIGTERM (night-window kill) unwinds the CDPBrowser context manager,
    # so Chrome closes and the temp profile is removed instead of leaking.
    def _term(signum, frame):
        raise SystemExit(143)
    signal.signal(signal.SIGTERM, _term)

    db = PropertyDatabase(os.path.join(ROOT, "data", "property_search.db"))
    predictor = RaceEthnicityPredictor()

    batch_id = None
    if input_path:
        from src.robust_database import SearchJobManager
        jm = SearchJobManager(db)
        print(f"loading streets from {input_path}")
        streets = jm.load_streets_from_excel(input_path)
        if not streets:
            print("no streets found in excel")
            return 1
        streets = list(dict.fromkeys(streets))  # dedup, keep order
        batch_id = jm.create_search_job(
            streets, name or f"Browser_{time.strftime('%Y%m%d_%H%M%S')}",
            force=force)
        print(f"created batch {batch_id}: {len(streets)} streets")

    if batch_id is None:
        # FIFO pick: oldest in_progress/interrupted batch with pending
        # streets (skip zombies with zero progress rows)
        batches = db.get_all_batches()  # started_at DESC
        incomplete = [b for b in batches if b['status'] in ('in_progress', 'interrupted')]
        for b in reversed(incomplete):
            prog = db.get_search_progress(b['id'])
            if prog['pending'] + prog['in_progress'] > 0:
                batch_id = b['id']
                break
    if batch_id is None:
        print("no pending batch — nothing to do")
        return 0

    prog = db.get_search_progress(batch_id)
    print(f"batch {batch_id}: {prog['pending']} pending / {prog['in_progress']} in_progress")

    db.reset_in_progress_streets(batch_id)
    done = 0
    with CDPBrowser() as b:
        print(f"landed: {b.goto_sdat()!r}")
        while (limit is None or done < limit):
            task = db.get_next_pending_street(batch_id)
            if not task:
                break
            street, county = task['street_name'], task['county']
            cid = county_id(county)
            if not cid:
                db.mark_street_completed(street, county, 0, error=f"county unmapped: {county}")
                continue
            try:
                # try the base spelling, then the proven variants — only
                # walked when the previous form returned nothing
                ok = False
                for variant in name_variants(street):
                    if b.search_street(variant, cid):
                        ok = True
                        break
            except BrowserBlockedError as e:
                print(f"BLOCKED mid-run after {done} streets: {e}")
                return 2
            records = []
            if ok:
                # poll: "Search Result for" text can render before the
                # grid table populates — don't race it
                grid = None
                for _ in range(15):
                    try:
                        grid = b.read_grid()
                    except Exception:
                        grid = None
                    if b.grid_parsed_ok(grid):
                        break
                    time.sleep(2)
                if not grid:
                    print("    (grid never rendered)")
                elif not grid.get('rows'):
                    print(f"    (grid but no rows: id={grid.get('id')!r})")
                if grid:
                    records = parse_grid(grid, street, county)
                    if not records:
                        print(f"    (parse 0 — rows head: "
                              f"{json.dumps(grid['rows'][:5], ensure_ascii=False)[:500]})")
                    # paginate while the pager offers the next page number;
                    # wait for the pager's current-page span to flip before
                    # reading (the old grid lingers during the postback)
                    seen_accounts = {r['account'] for r in records if r['account']}
                    page = 2
                    while True:
                        pager = grid.get('pager') or []
                        if not any(str(p.get('text')) == str(page)
                                   and p.get('clickable') for p in pager):
                            break
                        if not b.click_page(page):
                            break
                        if not b._wait_js(
                                f"""
                                (() => {{
                                    const cur = [...document.querySelectorAll('span')]
                                        .find(el => /^\\s*\\d+\\s*$/.test(el.innerText || '') && !el.id);
                                    return cur && cur.innerText.trim() === '{page}' ? 1 : 0;
                                }})()
                                """, timeout=30, poll=2):
                            break
                        g2 = b.read_grid()
                        if not g2:
                            break
                        for rec in parse_grid(g2, street, county):
                            if not rec['account'] or rec['account'] not in seen_accounts:
                                seen_accounts.add(rec['account'])
                                records.append(rec)
                        grid = g2
                        page += 1
                        if page > 500:
                            break

            # race prediction per owner
            for rec in records:
                pred = predictor.predict_race(rec['owner_name'])
                rec['predicted_race'] = pred.get('predicted_race')
                rec['race_confidence'] = pred.get('confidence')
                rec['race_method'] = pred.get('method')
                rec['is_hindu'] = pred.get('is_hindu', False)
                rec['sub_category'] = pred.get('sub_category')

            if replace and not records:
                deleted = db.delete_street_results(street, county)
                if deleted:
                    print(f"    replace: cleared {deleted} stale rows")
            if records:
                db.add_properties(records, batch_id, replace=replace)
            db.mark_street_completed(street, county, properties_found=len(records))
            done += 1
            print(f"  [{done}] {street} / {county}: {len(records)} properties"
                  + ("  (no grid)" if not ok else ""))
            # human-ish pacing between streets
            time.sleep(3 + (os.urandom(1)[0] / 255) * 5)

    prog = db.get_search_progress(batch_id)
    remaining = prog['pending'] + prog['in_progress']
    if remaining == 0:
        db.update_batch_progress(batch_id,
                                 streets_completed=prog['completed'],
                                 properties_found=prog['total_properties'],
                                 status='completed')
    try:
        print(f"backup: {db.backup_database()}")
    except Exception as e:
        print(f"backup failed: {e}")
    print(f"pass done: {done} streets this pass, {remaining} still pending")
    return 0


def parse_grid(grid, street, county):
    """Grid rows -> property records. Header row first; data rows follow.

    Columns: Name / Account / Street / Own Occ / Map / Parcel (order taken
    from the header row, not assumed — SDAT has rearranged before).
    """
    rows = grid.get('rows') or []
    if not rows:
        return []
    header = None
    hidx = None
    for i, r in enumerate(rows):
        labels = [c.lower().strip() for c in r]
        if 'name' in labels and 'parcel' in labels:
            header = labels
            hidx = i
            break
    if header is None:
        return []

    def col(*names):
        for n in names:
            if n in header:
                return header.index(n)
        return None

    c_name = col('name', 'owner name')
    c_acct = col('account', 'account number')
    c_street = col('street', 'street name')
    c_map = col('map')
    c_parcel = col('parcel')
    if c_name is None:
        return []

    records = []
    for r in rows[hidx + 1:]:
        if len(r) <= max(x for x in (c_name, c_acct, c_street, c_map, c_parcel) if x is not None):
            continue
        owner = (r[c_name] or '').strip()
        if not owner:
            continue
        def g(i):
            return (r[i] or '').strip() if i is not None and i < len(r) else ''
        map_parcel = ' '.join(x for x in (g(c_map), g(c_parcel)) if x)
        records.append({
            'street_name': street,
            'county': county,
            'owner_name': ' '.join(owner.split()),
            'address': g(c_street) or street,
            'city': '',
            'zip_code': '',
            'source_street': street,
            'account': g(c_acct),
            'map_parcel': map_parcel,
        })
    return records


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    d = sub.add_parser('dump', help='one search, dump grid DOM')
    d.add_argument('--street', required=True)
    d.add_argument('--county', required=True)
    r = sub.add_parser('run', help='drain oldest pending batch')
    r.add_argument('-i', '--input', default=None,
                   help='Excel file with streets — creates a new job first')
    r.add_argument('--force', action='store_true',
                   help='re-scrape streets already completed elsewhere')
    r.add_argument('--name', default=None, help='job name for -i')
    r.add_argument('--limit', type=int, default=None)
    r.add_argument('--replace', action='store_true')
    r.add_argument('--no-filter', action='store_true',
                   help='ignored — the ML no-result filter is not used by '
                        'the browser engine (accepted for arg compatibility '
                        'with robust_bulk_search.py invocations)')
    args = ap.parse_args()
    if args.cmd == 'dump':
        sys.exit(dump(args.street, args.county))
    sys.exit(run(limit=args.limit, replace=args.replace,
                 input_path=args.input, force=args.force, name=args.name))


if __name__ == "__main__":
    main()
