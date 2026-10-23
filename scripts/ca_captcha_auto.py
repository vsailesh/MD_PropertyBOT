#!/usr/bin/env python3
"""Fully-local reCAPTCHA v2 solver driver for Tyler self-service portals.

open -> shot -> (macOS OCR instruction + YOLOv8n tiles) -> click -> verify,
looping until g-recaptcha-response token appears, then clicks the
disclaimer Accept. Everything runs on-device (~1s/round), so the ~105s
challenge TTL never bites.

Usage: ca_captcha_auto.py [port]   (default 9228, SB profile chrome)
"""
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request

import numpy as np
import onnxruntime as ort
from PIL import Image
import websocket

sys.path.insert(0, "scripts")
from rc_yolo import COCO, SYN, detect  # noqa: E402

ROOT = __file__.rsplit("/", 2)[0]
PORT = sys.argv[1] if len(sys.argv) > 1 else "9228"
CDP = f"http://localhost:{PORT}"
SHOT = f"/tmp/tyler_auto_{PORT}.png"

# bootstrap: /tmp is wiped on reboot — restore model + build OCR binary
if not os.path.exists("/tmp/yolov8n.onnx") and os.path.exists(f"{ROOT}/data/yolov8n.onnx"):
    shutil.copyfile(f"{ROOT}/data/yolov8n.onnx", "/tmp/yolov8n.onnx")
if not os.path.exists("/tmp/rc_ocr"):
    subprocess.run(["swiftc", "-O", "-o", "/tmp/rc_ocr",
                    f"{ROOT}/scripts/rc_ocr.swift"], check=False)
SESS = ort.InferenceSession("/tmp/yolov8n.onnx",
                            providers=["CPUExecutionProvider"])

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


class WS:
    def __init__(self):
        self.connect()

    def connect(self):
        tabs = json.load(urllib.request.urlopen(f"{CDP}/json"))
        tab = next(t for t in tabs if t.get("type") == "page"
                   and (t.get("url") or "").startswith("http"))
        self.ws = websocket.create_connection(
            tab["webSocketDebuggerUrl"], timeout=90, origin=f"http://localhost:{PORT}")
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
        time.sleep(0.15)
        for evt in [{"type": "mousePressed", "x": cx, "y": cy, "button": "left", "clickCount": 1},
                    {"type": "mouseReleased", "x": cx, "y": cy, "button": "left", "clickCount": 1}]:
            self.cmd("Input.dispatchMouseEvent", evt)
        time.sleep(0.6)

    def token(self):
        return self.ev("""(()=>{const t=document.querySelector('#g-recaptcha-response'); return t? (t.value||'').length: 0;})()""")

    def bframe(self):
        s = self.ev(BF_JS)
        return json.loads(s) if s else None

    def open_challenge(self):
        v = self.bframe()
        if not v or v[1] <= 0:
            a = self.ev(ANCHOR_JS)
            if not a:
                return None
            a = json.loads(a)
            self.click(a[0] + 30, a[1] + a[3] / 2)
            for _ in range(20):
                time.sleep(1)
                v = self.bframe()
                if v and v[1] > 0:
                    break
        if not v or v[1] <= 0:
            return None
        self.ev("""(()=>{const A=[...document.querySelectorAll('iframe')].find(f=>f.src.includes('anchor')); if(A) A.style.pointerEvents='none'; return 1;})()""")
        time.sleep(1.2)
        return self.bframe()

    def shot(self, v):
        r = self.cmd("Page.captureScreenshot", {
            "format": "png",
            "clip": {"x": v[0], "y": v[1], "width": v[2], "height": v[3], "scale": 1}})
        open(SHOT, "wb").write(base64.b64decode(r["result"]["data"]))


def grid_geometry():
    """Detect grid rows via bright line scan; assume 3 or 4 cols evenly."""
    g = np.array(Image.open(SHOT).convert("L"))
    band = g[240:1080, :]
    rm = band.mean(axis=1)
    thr = rm.mean() + 2.2 * rm.std()
    cand = [240 + i for i in range(len(rm)) if rm[i] > thr]
    lines = []
    for y in cand:
        if not lines or y - lines[-1][-1] > 40:
            lines.append([y])
        else:
            lines[-1].append(y)
    GY = [int(np.mean(c)) for c in lines]
    if len(GY) < 4:  # detection failed -> standard 3x3
        GY = [247, 509, 769, 1042]
    n = len(GY) - 1
    GX = [14 + (786 - 14) * i // n for i in range(n + 1)]
    return GX, GY


def solve_round():
    """Returns list of (c,r) tiles + target word for current SHOT."""
    txt = subprocess.run(["/tmp/rc_ocr", SHOT], capture_output=True,
                         text=True).stdout.lower()
    target = ""
    for w in SYN:
        if w in txt and len(w) > len(target):
            target = w
    dets = detect(SESS, Image.open(SHOT).convert("RGB"))
    det_s = " ".join(f"{l}:{int(c*100)}@{int(b[0]+b[2]/2)},{int(b[1]+b[3]/2)}"
                     for l, c, b in dets)
    print("  OCR:", txt.strip()[:70], flush=True)
    print("  DET:", det_s[:200], flush=True)
    if not target:
        return None, "?"
    wanted = SYN[target]
    GX, GY = grid_geometry()
    n = len(GY) - 1
    tiles = set()
    for lbl, conf, (bx, by, bw, bh) in dets:
        if conf < 0.25:
            continue
        hit = lbl in wanted
        # rider heuristic: person sitting on bike-sized box when target is
        # bicycle/motorcycle and that label also fires weakly nearby
        if not hit and target in ("bicycle", "motorcycle") and lbl == "person":
            near = any(d[0] in ("bicycle", "motorcycle") and d[1] > 0.10 and
                       abs(d[2][0] - bx) < 120 and abs(d[2][1] - by) < 150
                       for d in dets)
            hit = conf > 0.40 and near
        if not hit:
            continue
        for px, py in [(bx + bw / 2, by + bh / 2),
                       (bx + bw / 2, by + bh * 0.25)]:
            c = next((i + 1 for i in range(n) if GX[i] <= px < GX[i + 1]), 0)
            r = next((i + 1 for i in range(n) if GY[i] <= py < GY[i + 1]), 0)
            if c and r:
                tiles.add((c, r))
    return sorted(tiles), target


def main():
    w = WS()
    for attempt in range(30):
        tok = w.token()
        if tok and tok > 200:
            print("TOKEN", tok, flush=True)
            break
        v = w.open_challenge()
        if not v:
            print("no challenge widget", flush=True)
            break
        w.shot(v)
        tiles, target = solve_round()
        if not tiles:
            # unknown target or no tiles: hit reload (bottom-left icon),
            # next round; bail if the grid stops changing
            w.click(v[0] + 30, v[1] + v[3] - 28)
            print(f"[{attempt}] {target} -> refresh (no tiles)", flush=True)
            time.sleep(3.0)
            import os
            sig = open(SHOT, "rb").read()[-2000:]
            if sig == globals().get("_last_sig"):
                globals()["_same"] = globals().get("_same", 0) + 1
            else:
                globals()["_same"] = 0
                globals()["_last_sig"] = sig
            if globals().get("_same", 0) >= 2:
                print("grid frozen — giving up this widget", flush=True)
                sys.exit(3)
            continue
        GX, GY = grid_geometry()
        n = len(GY) - 1
        cw = [(GX[i] + GX[i + 1]) / 2 for i in range(n)]
        ch = [(GY[i] + GY[i + 1]) / 2 for i in range(n)]
        for c, r in tiles:
            w.click(v[0] + cw[c - 1] / 2, v[1] + ch[r - 1] / 2)
        # verify (bottom-right of bframe)
        w.click(v[0] + 345, v[1] + 545)
        time.sleep(3)
        tok = w.token()
        print(f"[{attempt}] {target} tiles={tiles} token={tok}", flush=True)
        if tok and tok > 200:
            # accept disclaimer
            btn = w.ev("""(()=>{
              const b=[...document.querySelectorAll('input[type=submit],button')].find(e=>e.offsetParent&&/accept/i.test(e.value||e.innerText));
              if(!b) return '';
              b.scrollIntoView({block:'center'});
              const rc=b.getBoundingClientRect();
              return JSON.stringify([rc.x+rc.width/2, rc.y+rc.height/2]);
            })()""")
            if btn:
                bc = json.loads(btn)
                w.click(*bc)
                time.sleep(6)
            print("URL:", w.ev("location.href"), flush=True)
            print("SOLVED", flush=True)
            return
        time.sleep(1.0)
    print("gave up", flush=True)


if __name__ == "__main__":
    main()
