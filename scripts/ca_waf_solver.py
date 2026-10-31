#!/usr/bin/env python3
"""Amazon WAF captcha solver (amzn-captcha-modal) for Tyler portals that
gate pages with AWS WAF instead of reCAPTCHA (Monterey CA).

Widget: intro ("Let's confirm you are human" + Begin) -> one 320x320
canvas image with 9 role=checkbox buttons overlaid (3x3, row-major),
prompt "Choose all the <category>", Confirm button.

Classification: CLIP ViT-B/32 zero-shot per tile. YOLOv8n COCO could not
handle WAF categories outside COCO (curtains, hats, buckets, kites,
chimneys, bridges, mailboxes, stairs) and misfired on COCO ones; CLIP
scores each tile against "a photo of a <category>" prompts for the full
WAF pool plus an empty-background negative — select tiles where the
target category wins outright.

Gate mechanics learned live:
  - reload-from-intro loops forever; Begin-click opens the challenge
  - reload-from-modal rolls a fresh challenge (unknown category -> roll)
  - a pass needs 1-3 correct confirms
  - deep navigations can re-challenge; rc=3 from fetcher re-invokes this

Usage: ca_waf_solver.py <port> [target_url]
"""
import base64
import json
import os
import sys
import time
import urllib.request

import numpy as np
import torch
from PIL import Image
import websocket

ROOT = __file__.rsplit("/", 2)[0]
PORT = sys.argv[1] if len(sys.argv) > 1 else "9244"
CDP = f"http://localhost:{PORT}"
SHOT = f"/tmp/waf_{PORT}.png"

# WAF category pool (plural prompt word -> CLIP text word)
CATS = {
    "airplanes": "airplane", "bicycles": "bicycle", "boats": "boat",
    "buses": "bus", "cars": "car", "cats": "cat", "dogs": "dog",
    "fire hydrants": "fire hydrant", "motorcycles": "motorcycle",
    "trucks": "truck", "birds": "bird", "horses": "horse",
    "elephants": "elephant", "bears": "bear", "zebras": "zebra",
    "giraffes": "giraffe", "umbrellas": "umbrella", "bananas": "banana",
    "apples": "apple", "oranges": "orange fruit", "clocks": "clock",
    "books": "book", "chairs": "chair", "couches": "couch",
    "beds": "bed", "vases": "vase", "scissors": "scissors",
    "teddy bears": "teddy bear", "toothbrushes": "toothbrush",
    "traffic lights": "traffic light", "stop signs": "stop sign",
    "parking meters": "parking meter", "benches": "bench",
    "backpacks": "backpack", "handbags": "handbag", "bags": "handbag",
    "purses": "handbag", "suitcases": "suitcase", "skis": "skis",
    "snowboards": "snowboard", "skateboards": "skateboard",
    "surfboards": "surfboard", "bottles": "bottle",
    "wine glasses": "wine glass", "cups": "cup", "forks": "fork",
    "knives": "knife", "spoons": "spoon", "bowls": "bowl",
    "potted plants": "potted plant", "hats": "hat",
    "curtains": "curtain", "buckets": "bucket", "kites": "kite",
    "chimneys": "chimney", "bridges": "bridge", "mailboxes": "mailbox",
    "stairs": "stairs", "ladders": "ladder", "flags": "flag",
    "tents": "tent", "rocks": "rock", "mountains": "mountain",
    "trees": "tree", "flowers": "flower", "clouds": "cloud",
    "houses": "house", "buildings": "building", "keys": "key",
    "lamps": "lamp", "shoes": "shoe", "socks": "sock",
    "shirts": "shirt", "plates": "plate", "candles": "candle",
    "bells": "bell", "drums": "drum", "guitars": "guitar",
    "cameras": "camera", "phones": "phone", "laptops": "laptop",
    "televisions": "television", "refrigerators": "refrigerator",
    "ovens": "oven", "microwaves": "microwave", "sinks": "sink",
    "toilets": "toilet", "doors": "door", "windows": "window",
    "fences": "fence", "walls": "wall", "streets": "street",
    "sidewalks": "sidewalk",
}

import open_clip  # noqa: E402

CACHE = f"{ROOT}/data/models/clip_b32"
model, _, preprocess = open_clip.create_model_and_transforms(
    "ViT-B-32", pretrained="openai", cache_dir=CACHE)
tokenizer = open_clip.get_tokenizer("ViT-B-32")
model.eval()

# precompute normalized text embeddings: all categories + empty negative.
# prompt-ensemble (4 templates averaged) buys a few accuracy points on
# the ~107px tiles vs a single "a photo of a X" prompt
_WORDS = sorted(set(CATS.values()))
_TEMPLATES = ["a photo of a {}",
              "a photo of a single {}",
              "a close-up photo of a {}",
              "a photo of one {}"]
with torch.no_grad():
    cols = []
    for tpl in _TEMPLATES:
        _t = tokenizer([tpl.format(w) for w in _WORDS]
                       + ["a photo of an empty plain background"])
        _e = model.encode_text(_t).float()
        _e = _e / _e.norm(dim=-1, keepdim=True)
        cols.append(_e)
    _e = torch.stack(cols).mean(dim=0)
    _e = _e / _e.norm(dim=-1, keepdim=True)
TEXT_EMB = _e.numpy()
EMPTY_IDX = len(_WORDS) - 1  # index of empty negative in _WORDS order
# map CLIP word -> row index in TEXT_EMB
_WIDX = {w: i for i, w in enumerate(_WORDS)}


def clip_tiles(img, target_word):
    """Zero-shot classify the 3x3 tiles; return tile indices (0-8,
    row-major) where target wins outright over every other category and
    the empty negative."""
    W, H = img.size
    tw, th = W // 3, H // 3
    batch = []
    for r in range(3):
        for c in range(3):
            tile = img.crop((c * tw, r * th, (c + 1) * tw, (r + 1) * th))
            batch.append(preprocess(tile))
    with torch.no_grad():
        ie = model.encode_image(torch.stack(batch)).float()
        ie = ie / ie.norm(dim=-1, keepdim=True)
    sims = ie.numpy() @ TEXT_EMB.T  # [9, n_words+1]
    ti = _WIDX[target_word]
    # margin rule: target must beat the best OTHER category by >0.02;
    # argmax alone over-selects (5+ tiles) because objects outside the
    # category list bleed onto the target class
    margins = []
    for k in range(9):
        row = sims[k].copy()
        t = row[ti]
        row[ti] = -9.0
        row[-1] = -9.0  # empty negative
        margins.append(float(t - row.max()))
    order = np.argsort(margins)[::-1]
    picks = [int(k) for k in order[:4] if margins[k] > 0.03]
    if not picks and margins[order[0]] > 0.005:
        picks = [int(order[0])]
    return picks


class WS:
    def __init__(self, target=None):
        self.connect(target)

    def connect(self, target=None):
        tabs = json.load(urllib.request.urlopen(f"{CDP}/json"))
        tab = next((t for t in tabs if t.get("type") == "page"
                    and (t.get("url") or "").startswith("http")), None)
        if not tab:
            # fresh chrome (about:blank): open the target directly
            if not target:
                print("no http tab and no target URL", flush=True)
                sys.exit(6)
            req = urllib.request.Request(
                f"{CDP}/json/new?{target}", method="PUT")
            tab = json.load(urllib.request.urlopen(req))
            time.sleep(8)
        self.ws = websocket.create_connection(
            tab["webSocketDebuggerUrl"], timeout=90,
            origin=f"http://localhost:{PORT}")
        self.n = 0

    def cmd(self, method, params=None):
        self.n += 1
        i = self.n
        self.ws.send(json.dumps({"id": i, "method": method,
                                 "params": params or {}}))
        while True:
            m = json.loads(self.ws.recv())
            if m.get("id") == i:
                return m

    def ev(self, e):
        return self.cmd("Runtime.evaluate", {"expression": e,
                                             "returnByValue": True}
                        ).get("result", {}).get("result", {}).get("value")

    def click(self, cx, cy):
        self.cmd("Input.dispatchMouseEvent",
                 {"type": "mouseMoved", "x": cx, "y": cy})
        time.sleep(0.1)
        for evt in [{"type": "mousePressed", "x": cx, "y": cy,
                     "button": "left", "clickCount": 1},
                    {"type": "mouseReleased", "x": cx, "y": cy,
                     "button": "left", "clickCount": 1}]:
            self.cmd("Input.dispatchMouseEvent", evt)
        time.sleep(0.3)

    def click_center_js(self, js):
        v = self.ev(js)
        if not v:
            return False
        self.click(*json.loads(v))
        return True


CANVAS_JS = """(()=>{const c=document.querySelector('.amzn-captcha-modal canvas');
  if(!c) return ''; const r=c.getBoundingClientRect();
  return JSON.stringify([r.x,r.y,r.width,r.height]);})()"""
PROMPT_JS = """(()=>{const m=document.querySelector('.amzn-captcha-modal');
  if(!m) return ''; const t=m.innerText||''; const mm=t.match(/choose all\\s+(?:the\\s+)?([a-z ]+)/i);
  return mm? mm[1].trim().toLowerCase() : '';})()"""
BUTTONS_JS = """(()=>{const bs=[...document.querySelectorAll('button[role=checkbox]')];
  return JSON.stringify(bs.map(b=>{const r=b.getBoundingClientRect();
    return [Math.round(r.x+r.width/2), Math.round(r.y+r.height/2)];}));})()"""
CONFIRM_JS = """(()=>{const b=[...document.querySelectorAll('button')].find(x=>/^confirm$/i.test((x.innerText||'').trim())&&x.offsetParent);
  if(!b) return ''; const r=b.getBoundingClientRect();
  return JSON.stringify([Math.round(r.x+r.width/2),Math.round(r.y+r.height/2)]);})()"""
BEGIN_JS = """(()=>{const b=[...document.querySelectorAll('button')].find(x=>/^begin$/i.test((x.innerText||'').trim())&&x.offsetParent);
  if(!b) return ''; const r=b.getBoundingClientRect();
  return JSON.stringify([Math.round(r.x+r.width/2),Math.round(r.y+r.height/2)]);})()"""


def main():
    target = sys.argv[2] if len(sys.argv) > 2 else None
    w = WS(target)
    # blank/wedged renderer after a chrome restart: navigate to target
    href = w.ev("location.href") or ""
    if target and "tylerhost" not in href:
        w.cmd("Page.navigate", {"url": target})
        time.sleep(8)
    unknown = 0
    for attempt in range(40):
        # widget present?
        if not w.ev("(()=>{return document.querySelector('.amzn-captcha-modal')?1:0;})()"):
            body = w.ev("document.body.innerText") or ""
            href = w.ev("location.href") or ""
            # passed: Tyler content rendered OR deep URL actually resolved
            # (WAF intro keeps the target URL but shows "confirm you are
            # human" body — a resolved deep page has real Tyler content)
            if ("Disclaimer" in body or "I Accept" in body or w.ev(
                    "(()=>{return document.getElementById('field_BothNamesID')?1:0;})()")
                    or ("/action/" in href or "/search/" in href)
                    and "confirm you are human" not in body):
                break
            # intro state ("Begin" button): Begin opens the challenge
            # modal; reload-from-intro loops forever (only
            # reload-from-modal rolls a fresh challenge or passes)
            if "confirm you are human" in body or "security check" in body:
                if not w.click_center_js(BEGIN_JS):
                    w.cmd("Page.reload")
                    time.sleep(5)
                else:
                    time.sleep(3)
                continue
            if not w.click_center_js(BEGIN_JS):
                time.sleep(2)
            else:
                time.sleep(3)
            continue
        # dead-challenge states: expired timer or still loading have
        # no live buttons — every confirm on them silently no-ops, which
        # is what burned 40 rounds into "gave up" before
        mtext = w.ev("""(()=>{const m=document.querySelector('.amzn-captcha-modal');
          return m? (m.innerText||'').slice(0,200):'';})()""") or ""
        # "Loading" alone = normal post-confirm transition (the next
        # challenge renders within a few seconds); only reload when the
        # timer expired or loading has no live buttons behind it
        if "time limit exceeded" in mtext.lower() or (
                "loading" in mtext.lower()
                and json.loads(w.ev(BUTTONS_JS) or "[]") == []):
            print("challenge dead (timer/loading) -> reload", flush=True)
            w.cmd("Page.reload")
            time.sleep(4)
            continue
        prompt = w.ev(PROMPT_JS) or ""
        c = w.ev(CANVAS_JS)
        bs0 = w.ev(BUTTONS_JS) or "[]"
        if not prompt or not c or json.loads(bs0) == []:
            print("no widget geometry yet", flush=True)
            time.sleep(2)
            continue
        c = json.loads(c)
        word = CATS.get(prompt)
        print(f"[{attempt}] prompt={prompt!r} word={word}", flush=True)
        if not word:
            unknown += 1
            if unknown >= 25:
                print("too many unknown categories", flush=True)
                sys.exit(3)
            # unknown category: reload page for a new challenge
            w.cmd("Page.reload")
            time.sleep(5)
            continue
        # scale 2: 320 CSS px -> 640 device px, ~213px tiles (CLIP does
        # better on 213px crops than 107px)
        r = w.cmd("Page.captureScreenshot", {
            "format": "png",
            "clip": {"x": c[0], "y": c[1], "width": c[2], "height": c[3],
                     "scale": 2}})
        open(SHOT, "wb").write(base64.b64decode(r["result"]["data"]))
        tiles = clip_tiles(Image.open(SHOT).convert("RGB"), word)
        print(f"  tiles={tiles}", flush=True)
        if not tiles:
            # nothing found (missed detection): confirming zero = fail;
            # reload for a fresh challenge instead
            unknown += 1
            w.cmd("Page.reload")
            time.sleep(5)
            continue
        bs = json.loads(w.ev(BUTTONS_JS) or "[]")
        for t in tiles:
            w.click(*bs[t])
        time.sleep(0.3)
        cb = w.ev(CONFIRM_JS)
        if cb:
            w.click(*json.loads(cb))
        time.sleep(4)
    # after gate: disclaimer accept if present
    for _ in range(10):
        body = w.ev("document.body.innerText") or ""
        if w.ev("(()=>{return document.getElementById('field_BothNamesID')?1:0;})()"):
            print("URL:", w.ev("location.href"), flush=True)
            print("GATE PASSED", flush=True)
            return
        if "Disclaimer" in body and "I Accept" in body:
            w.ev("""(()=>{const b=document.getElementById('submitDisclaimerAccept');
              if(b&&b.offsetParent){b.click(); return 1;} return 0;})()""")
            time.sleep(6)
            continue
        # google recaptcha present too? leave to ca_captcha_auto
        if w.ev("""(()=>{return [...document.querySelectorAll('iframe')].some(f=>(f.src||'').includes('recaptcha'));})()"""):
            print("recaptcha gate behind WAF — run ca_captcha_auto", flush=True)
            return
        time.sleep(3)
    print("gave up", flush=True)


if __name__ == "__main__":
    main()
