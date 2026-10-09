#!/usr/bin/env python3
"""Harvest a Cloudflare cf_clearance cookie for SDAT using a real browser.

The SDAT WAF (since Oct 2026) 403s every automated POST — requests,
curl_cffi with Chrome TLS, even Selenium (webdriver detected). Only an
organic browser passes the challenge. This script:

  1. launches the user's real Brave with a throwaway profile and a CDP
     debug port (no webdriver flags — Cloudflare sees a normal browser),
  2. navigates to SDAT and waits out the challenge,
  3. reads the cf_clearance + __cf_bm cookies and exact UA string via
     CDP,
  4. writes them to data/.sdat_clearance.json for the scraper to load,
  5. closes Brave and removes the temp profile.

The scraper (curl_cffi) replays the cookie with the matching UA; the
clearance is IP+UA bound (same machine, fine) and lives ~30-60 min, so
the watchdog harvests a fresh one before each pass.

Usage: python scripts/sdat_clearance.py [--force]
  --force  harvest even if the cached clearance is still fresh
Exit 0 = clearance file fresh/written; 1 = harvest failed.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "data", ".sdat_clearance.json")
FRESH_SECS = 45 * 60  # re-harvest under this age
PORT = 9223
SDAT = "https://sdat.dat.maryland.gov/RealProperty/Pages/default.aspx"
# First installed browser wins (Chrome lives on the boot volume; Brave is
# on the external disk and vanishes with it).
_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Volumes/HulkBuster/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
]
BRAVE = next((p for p in _CANDIDATES if os.path.exists(p)), _CANDIDATES[0])


def load_cached():
    try:
        with open(OUT) as f:
            d = json.load(f)
        if time.time() - d["ts"] < FRESH_SECS:
            return d
    except Exception:
        pass
    return None


def http_json(path):
    with urllib.request.urlopen(f"http://127.0.0.1:{PORT}{path}", timeout=5) as r:
        return json.loads(r.read().decode())


def wait_port(timeout=15):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            http_json("/json/version")
            return True
        except Exception:
            time.sleep(0.5)
    return False


def harvest():
    import websocket  # websocket-client

    profile = tempfile.mkdtemp(prefix="sdat_brave_")
    args = [
        BRAVE,
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-brave-update",
        f"--remote-debugging-port={PORT}",
        "--remote-allow-origins=*",
        "--window-size=1100,850",
        "about:blank",
    ]
    proc = subprocess.Popen(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        if not wait_port():
            print("harvest: CDP port never came up")
            return None

        # attach to the initial about:blank target
        target = None
        for _ in range(20):
            for t in http_json("/json/list"):
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    target = t
                    break
            if target:
                break
            time.sleep(0.5)
        if not target:
            print("harvest: no page target")
            return None

        ws = websocket.create_connection(target["webSocketDebuggerUrl"], timeout=30)
        mid = 0

        def cmd(method, **params):
            nonlocal mid
            mid += 1
            ws.send(json.dumps({"id": mid, "method": method, "params": params}))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == mid:
                    return msg.get("result", {})

        cmd("Page.enable")
        cmd("Network.enable")
        cmd("Page.navigate", url=SDAT)

        # Wait out the Cloudflare interstitial: title flips from
        # "Just a moment..." to the real page once cleared.
        deadline = time.time() + 60
        title = ua = ""
        while time.time() < deadline:
            time.sleep(3)
            try:
                r = cmd("Runtime.evaluate", expression="document.title")
                title = (r.get("result") or {}).get("value", "")
            except Exception:
                continue
            if title and "just a moment" not in title.lower():
                break
        if not title or "just a moment" in title.lower():
            print(f"harvest: challenge never cleared (title={title!r})")
            ws.close()
            return None

        ua = cmd("Runtime.evaluate", expression="navigator.userAgent")["result"]["value"]
        cookies = cmd("Network.getCookies", urls=[SDAT])["cookies"]
        jar = {c["name"]: c["value"] for c in cookies}
        ws.close()

        # Modern low-friction CF passes issue only __cf_bm; full managed
        # challenges also hand out cf_clearance. Take whichever exists.
        if "cf_clearance" not in jar and "__cf_bm" not in jar:
            print(f"harvest: cleared but no CF cookies (cookies: {list(jar)})")
            return None
        return {"cf_clearance": jar.get("cf_clearance", ""),
                "cf_bm": jar.get("__cf_bm", ""),
                "ua": ua, "ts": int(time.time())}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        time.sleep(1)
        shutil.rmtree(profile, ignore_errors=True)


def main():
    force = "--force" in sys.argv
    if not force:
        cached = load_cached()
        if cached:
            age = int(time.time() - cached["ts"])
            print(f"clearance fresh ({age // 60} min old) — skipping harvest")
            return 0
    d = harvest()
    if not d:
        return 1
    with open(OUT, "w") as f:
        json.dump(d, f)
    os.chmod(OUT, 0o600)
    print(f"clearance harvested (ua={d['ua'][:40]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
