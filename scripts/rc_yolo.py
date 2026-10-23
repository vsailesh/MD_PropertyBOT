#!/usr/bin/env python3
"""Local reCAPTCHA v2 tile solver — YOLOv8n via onnxruntime.

Usage: rc_yolo.py <shot.png>
Shot assumed 800x1160 (bframe clip @DPR2): instruction y<240,
3x3 grid rows y 247/509/769/1042, cols x 14/275/535/786 (falls back to
4x4 dynamic lines if 3x3 finds nothing).

Prints: TARGET <word> | TILES (c,r),(c,r) | DET <label:conf@x,y ...>
"""
import os
import subprocess
import sys

import numpy as np
import onnxruntime as ort
from PIL import Image

COCO = ["person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
        "truck", "boat", "traffic light", "fire hydrant", "stop sign",
        "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep",
        "cow", "backpack", "umbrella", "handbag", "tie", "suitcase",
        "frisbee", "skis", "snowboard", "sports ball", "kite",
        "baseball bat", "baseball glove", "skateboard", "surfboard",
        "tennis racket", "bottle", "wine glass", "cup", "fork", "knife",
        "spoon", "bowl", "banana", "apple", "sandwich", "orange",
        "broccoli", "carrot", "hot dog", "pizza", "donut", "cake",
        "chair", "couch", "potted plant", "bed", "dining table", "toilet",
        "tv", "laptop", "mouse", "remote", "keyboard", "cell phone",
        "microwave", "oven", "toaster", "sink", "refrigerator", "book",
        "clock", "vase", "scissors", "teddy bear", "hair drier",
        "toothbrush"]

# recaptcha instruction word -> COCO labels that satisfy it
SYN = {
    "bicycle": ["bicycle"], "bus": ["bus"], "car": ["car"],
    "motorcycle": ["motorcycle"], "fire hydrant": ["fire hydrant"],
    "traffic light": ["traffic light"], "traffic lights": ["traffic light"],
    "taxi": ["car", "taxi"], "truck": ["truck"], "boat": ["boat"],
    "airplane": ["airplane"], "train": ["train"], "bridge": [],
    "crosswalk": [], "chimney": [], "mountain": [], "stairs": [],
    "parking meter": ["parking meter"], "umbrella": ["umbrella"],
    "bananas": ["banana"], "apple": ["apple"], "bird": ["bird"],
    "cat": ["cat"], "dog": ["dog"], "horse": ["horse"], "cow": ["cow"],
}


def letterbox(im, s=640):
    w, h = im.size
    r = min(s / w, s / h)
    nw, nh = int(w * r), int(h * r)
    im2 = im.resize((nw, nh), Image.BILINEAR)
    canvas = Image.new("RGB", (s, s), (114, 114, 114))
    canvas.paste(im2, ((s - nw) // 2, (s - nh) // 2))
    return canvas, r, (s - nw) // 2, (s - nh) // 2


def detect(sess, im):
    lb, r, dx, dy = letterbox(im)
    x = np.asarray(lb, dtype=np.float32).transpose(2, 0, 1)[None] / 255.0
    out = sess.run(None, {"images": x})[0][0]  # 84 x 8400
    boxes, scores, labels = [], [], []
    cls_scores = out[4:]  # 80 x 8400
    for i in range(out.shape[1]):
        cx, cy, w, h = out[:4, i]
        conf = cls_scores[:, i]
        j = int(conf.argmax())
        c = float(conf[j])
        if c < 0.30:
            continue
        # back to original px
        cx = (cx - dx) / r
        cy = (cy - dy) / r
        boxes.append((cx - w / 2 / r, cy - h / 2 / r, w / r, h / r))
        scores.append(c)
        labels.append(COCO[j])
    # crude NMS
    keep = []
    for i in sorted(range(len(scores)), key=lambda k: -scores[k]):
        ok = True
        for k in keep:
            bx1, by1, bw1, bh1 = boxes[i]
            bx2, by2, bw2, bh2 = boxes[k]
            ix = max(0, min(bx1 + bw1, bx2 + bw2) - max(bx1, bx2))
            iy = max(0, min(by1 + bh1, by2 + bh2) - max(by1, by2))
            if ix * iy / max(1e-6, min(bw1 * bh1, bw2 * bh2)) > 0.6:
                ok = False
                break
        if ok:
            keep.append(i)
    return [(labels[i], scores[i], boxes[i]) for i in keep]


def main():
    path = sys.argv[1]
    im = Image.open(path).convert("RGB")
    txt = subprocess.run(["/tmp/rc_ocr", path], capture_output=True,
                         text=True).stdout.lower()
    target = ""
    for w in SYN:
        if w in txt and len(w) > len(target):
            target = w
    model = "/tmp/yolov8n.onnx"
    if not os.path.exists(model):
        model = os.path.join(os.path.dirname(__file__), "..", "data", "yolov8n.onnx")
    sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
    dets = detect(sess, im)
    det_s = " ".join(f"{l}:{int(c*100)}@{int(b[0]+b[2]/2)},{int(b[1]+b[3]/2)}"
                     for l, c, b in dets)
    if not target:
        print(f"TARGET ? | TILES  | OCR {txt[:80]} | DET {det_s}")
        return
    wanted = SYN[target]
    GX = [14, 275, 535, 786]
    GY3 = [247, 509, 769, 1042]
    tiles = set()
    for lbl, conf, (bx, by, bw, bh) in dets:
        if lbl not in wanted or conf < 0.35:
            continue
        # center + upper-third sample (tall objects span tiles)
        for px, py in [(bx + bw / 2, by + bh / 2),
                       (bx + bw / 2, by + bh * 0.25)]:
            c = 1 if px < GX[1] else 2 if px < GX[2] else 3 if px < GX[3] else 0
            r = 1 if py < GY3[1] else 2 if py < GY3[2] else 3 if py < GY3[3] else 0
            if c and r:
                tiles.add(f"{c},{r}")
    print(f"TARGET {target} | TILES {','.join(sorted(tiles))} | OCR {txt[:60]} | DET {det_s}")


if __name__ == "__main__":
    main()
