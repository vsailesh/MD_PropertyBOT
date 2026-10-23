#!/usr/bin/env python3
"""reCAPTCHA v2 challenge cycle helper for Tyler self-service counties
(SB/Fresno/Kern — same platform as Ventura).

Subcommands (each = one tight CDP step; call from shell between vision
calls — challenge images expire ~2min, so keep vision round-trips short):

  open    restore anchor PE -> click anchor -> wait bframe visible ->
          force bframe pointerEvents/z (SB page CSS buries it; without
          this clicks hit DIV.ss-page / the anchor iframe and either do
          nothing or toggle the challenge closed) -> screenshot -> print
          bf json
  shot    re-screenshot current bframe (assumes PE already fixed)
  click   args: img_x,img_y[ img_x,img_y ...] image-px coords (800x1160
          screenshot space, DPR=2) -> CSS /2 + bf origin -> click each
  verify  click the verify/skip button (bottom-right of bframe)
  token   print g-recaptcha-response length + bframe y

Files: /tmp/tyler_bf_<port>.json (bframe rect), /tmp/tyler_cap_<port>.png
"""
import base64
import json
import sys
import time
import urllib.request

import websocket

PORT = sys.argv[2] if len(sys.argv) > 2 else "9228"
CDP = f"http://localhost:{PORT}"
BFJ = f"/tmp/tyler_bf_{PORT}.json"
SHOT = f"/tmp/tyler_cap_{PORT}.png"


def get_tab():
    tabs = json.load(urllib.request.urlopen(f"{CDP}/json"))
    # first tab that is NOT devtools/blank and not an iframe target
    for t in tabs:
        if t.get("type") == "page" and (t.get("url") or "").startswith("http"):
            return t
    raise SystemExit("no tab")


class WS:
    def __init__(self):
        self.ws = websocket.create_connection(
            get_tab()["webSocketDebuggerUrl"], timeout=90, origin=f"http://localhost:{PORT}")
        self.n = 0

    def cmd(self, method, params=None):
        self.n += 1
        i = self.n
        self.ws.send(json.dumps({"id": i, "method": method, "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == i:
                return m

    def ev(self, e):
        return self.cmd("Runtime.evaluate", {"expression": e,
                                             "returnByValue": True}
                        ).get("result", {}).get("result", {}).get("value")

    def click(self, cx, cy):
        self.cmd("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": cx, "y": cy})
        time.sleep(0.2)
        for evt in [{"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1},
                    {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1}]:
            self.cmd("Input.dispatchMouseEvent", evt)
        time.sleep(0.8)

    def shot(self, v):
        r = self.cmd("Page.captureScreenshot", {
            "format": "png",
            "clip": {"x": v[0], "y": v[1], "width": v[2], "height": v[3], "scale": 1}})
        open(SHOT, "wb").write(base64.b64decode(r["result"]["data"]))


BF_JS = """(()=>{
  const B=[...document.querySelectorAll('iframe')].find(f=>f.src.includes('bframe'));
  if(!B) return '';
  B.style.pointerEvents='auto'; B.style.zIndex=2147483647;
  const r=B.getBoundingClientRect();
  return JSON.stringify([r.x,r.y,r.width,r.height]);
})()"""

ANCHOR_JS = """(()=>{
  const A=[...document.querySelectorAll('iframe')].find(f=>f.src.includes('anchor'));
  if(!A) return '';
  A.style.pointerEvents='auto';
  const r=A.getBoundingClientRect();
  return JSON.stringify([r.x,r.y,r.width,r.height]);
})()"""


def main():
    op = sys.argv[1]
    w = WS()
    if op == "open":
        # challenge closed? click anchor to open
        v = json.loads(w.ev(BF_JS) or "null") if w.ev(BF_JS) else None
        if not v or v[1] <= 0:
            a = json.loads(w.ev(ANCHOR_JS) or "null") if w.ev(ANCHOR_JS) else None
            if not a:
                print("NOANCHOR"); return
            w.click(a[0] + 30, a[1] + a[3] / 2)
            v = None
            for _ in range(20):
                time.sleep(1)
                s = w.ev(BF_JS)
                if s:
                    cand = json.loads(s)
                    if cand[1] > 0:
                        v = cand
                        break
        else:
            w.ev(BF_JS)  # already open: just keep PE fixed
        if not v or v[1] <= 0:
            print("NOCHALLENGE"); return
        # neutralize anchor so mis-clicks can't toggle the challenge shut
        w.ev("""(()=>{const A=[...document.querySelectorAll('iframe')].find(f=>f.src.includes('anchor')); if(A) A.style.pointerEvents='none'; return 1;})()""")
        time.sleep(1.5)  # let tiles render
        json.dump(v, open(BFJ, "w"))
        w.shot(v)
        print("OK", v, time.strftime("%H:%M:%S"))
    elif op == "shot":
        v = json.loads(open(BFJ).read())
        v = json.loads(w.ev(BF_JS) or "null") or v
        json.dump(v, open(BFJ, "w"))
        w.shot(v)
        print("OK", time.strftime("%H:%M:%S"))
    elif op == "click":
        live = json.loads(w.ev(BF_JS) or "null") if w.ev(BF_JS) else None
        if not live or live[1] <= 0:
            print("CLOSED — reopen before clicking"); return
        json.dump(live, open(BFJ, "w"))
        v = live
        for pair in sys.argv[3:]:
            x, y = (float(z) for z in pair.split(","))
            w.click(v[0] + x / 2, v[1] + y / 2)
        print("clicked", len(sys.argv) - 3)
    elif op == "verify":
        live = json.loads(w.ev(BF_JS) or "null") if w.ev(BF_JS) else None
        if not live or live[1] <= 0:
            print("CLOSED — nothing to verify"); return
        v = live
        # verify button = bottom-right of bframe (~x 690/2, y 1090/2 in img px)
        w.click(v[0] + 345, v[1] + 545)
        time.sleep(3)
        tok = w.ev("(()=>{const t=document.querySelector('#g-recaptcha-response'); return t? (t.value||'').length: -1;})()")
        bf = w.ev(BF_JS)
        print("token:", tok, "bf:", bf, time.strftime("%H:%M:%S"))
    elif op == "grid":
        # shot grid-only crop (no bframe chrome) -> faster vision upload
        v = json.loads(w.ev(BF_JS) or "null") or json.load(open(BFJ))
        json.dump(v, open(BFJ, "w"))
        w.shot(v)
        from PIL import Image
        im = Image.open(SHOT)
        im.crop((6, 240, 794, 1050)).save(SHOT)
        print("OK", time.strftime("%H:%M:%S"))
    elif op == "delta":
        # shot grid crop, diff vs previous -> save only REPLACED tiles
        import numpy as np
        from PIL import Image, ImageChops
        prev_p, cur_p = SHOT + ".prev", SHOT
        import os
        had_prev = os.path.exists(prev_p)
        v = json.loads(w.ev(BF_JS) or "null") or json.load(open(BFJ))
        json.dump(v, open(BFJ, "w"))
        w.shot(v)
        im = Image.open(SHOT)
        im.crop((6, 240, 794, 1050)).save(cur_p)
        if not had_prev:
            import shutil
            shutil.copyfile(cur_p, prev_p)
            print("FIRST", time.strftime("%H:%M:%S")); return
        a = Image.open(prev_p).convert("RGB"); b = Image.open(cur_p).convert("RGB")
        if a.size != b.size: b = b.resize(a.size)
        A, B = np.array(a).astype(int), np.array(b).astype(int)
        d = np.abs(A - B).sum(axis=2)
        # grid in cropped coords: y 247-240=7..802? full-grid rows 7,275,543,810 / cols 8,269,529,780
        GX, GY = [8, 269, 529, 780], [7, 275, 543, 810]
        tiles = []
        W = 250
        canvas = Image.new("RGB", (3 * W, 0))
        comps = []
        for ri in range(3):
            for ci in range(3):
                t = d[GY[ri]:GY[ri + 1], GX[ci]:GX[ci + 1]]
                if (t > 40).mean() > 0.30:
                    comps.append((ri, ci, b.crop((GX[ci], GY[ri], GX[ci + 1], GY[ri + 1]))))
        if not comps:
            import shutil
            shutil.copyfile(cur_p, prev_p)
            print("NOCHANGE", time.strftime("%H:%M:%S")); return
        canvas = Image.new("RGB", (W, W * len(comps)))
        for k, (ri, ci, cimg) in enumerate(comps):
            canvas.paste(cimg.resize((W, W)), (0, k * W))
        canvas.save(SHOT + ".tiles.png")
        import shutil
        shutil.copyfile(cur_p, prev_p)
        print("NEW-TILES", [(ci + 1, ri + 1) for ri, ci, _ in comps], time.strftime("%H:%M:%S"))
    elif op == "token":
        tok = w.ev("(()=>{const t=document.querySelector('#g-recaptcha-response'); return t? (t.value||'').length: -1;})()")
        bf = w.ev(BF_JS)
        print("token:", tok, "bf:", bf)
    elif op == "marks":
        # objective: which 3x3 tiles have blue selection overlay
        import numpy as np
        from PIL import Image
        im = np.array(Image.open(SHOT).convert("RGB")).astype(int)
        r, g, b = im[:, :, 0], im[:, :, 1], im[:, :, 2]
        sel = (b > 190) & (b - r > 15)
        for cx, cy in [((14, 275, 535, 786), (247, 509, 769, 1042)),
                       ((14, 275, 535, 786), (255, 513, 771, 1029))]:
            print("grid", cy)
            for ri in range(3):
                print("  ", ["%.1f%%" % (100.0 * sel[cy[ri]:cy[ri+1], cx[ci]:cx[ci+1]].sum()
                                          / max(1, sel[cy[ri]:cy[ri+1], cx[ci]:cx[ci+1]].size))
                             for ci in range(3)])
            break
    elif op == "accept":
        # click the disclaimer accept submit once token present
        r = w.ev("""(()=>{
          const b=[...document.querySelectorAll('input[type=submit],button')].find(e=>e.offsetParent&&/accept/i.test(e.value||e.innerText));
          if(!b) return '';
          b.scrollIntoView({block:'center'});
          const rc=b.getBoundingClientRect();
          return JSON.stringify([rc.x+rc.width/2, rc.y+rc.height/2]);
        })()""")
        if not r:
            print("NOBTN"); return
        c = json.loads(r)
        w.click(*c)
        time.sleep(5)
        print("URL:", w.ev("location.href"))


if __name__ == "__main__":
    main()
