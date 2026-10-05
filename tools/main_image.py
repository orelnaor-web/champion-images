#!/usr/bin/env python3
"""Main photo per item: the illustration / the item alone first, model photos last.

Model photos show more than one item (top + pants, shoes…), so the app and the
catalogs open each item on its illustration. Writes order.json = {key: [n, …]}
(1-based photo numbers in display order) only for items whose order changes;
the app (click-catalog) reads it next to manifest.json.

Person detection: EfficientDet-Lite2 (COCO) via LiteRT. Results per photo are
cached in order-cache.json by file hash, so a run only looks at new photos.

Usage: python3 tools/main_image.py <model.tflite>     (run from the repo root)
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from ai_edge_litert.interpreter import Interpreter

PERSON = 0.45  # packshots score ≤0.3, photos with a model ≥0.55
_it = None


def person_score(im, model):
    global _it
    if _it is None:
        _it = Interpreter(model)
        _it.allocate_tensors()
    inp = _it.get_input_details()[0]
    s = max(im.size)
    sq = Image.new("RGB", (s, s), im.getpixel((0, 0)))
    sq.paste(im, ((s - im.width) // 2, (s - im.height) // 2))
    x = (np.asarray(sq.resize((448, 448)), dtype=np.float32) - 127.5) / 127.5
    _it.set_tensor(inp["index"], x[None])
    _it.invoke()
    outs = {o["name"]: _it.get_tensor(o["index"]) for o in _it.get_output_details()}
    cls = next(v for v in outs.values() if v.shape[-1] == 90)[0]
    return float(cls[:, 0].max())  # COCO class 0 = person


def drawing(im):
    """Flat drawing on a pure white / black background."""
    t = im.copy()
    t.thumbnail((200, 200))
    a = np.asarray(t).astype(np.int16)
    q = (a // 16).reshape(-1, 3)
    counts = np.bincount(q[:, 0] * 256 + q[:, 1] * 16 + q[:, 2], minlength=4096)
    flat = np.sort(counts)[::-1][:12].sum() / q.shape[0]
    border = np.concatenate([a[0], a[-1], a[:, 0], a[:, -1]])
    pure = ((border >= 250).all(axis=-1) | (border <= 8).all(axis=-1)).mean()
    black = (a <= 8).all(axis=-1).mean()
    return {"flat": float(flat), "pure": float(pure), "black": float(black)}


def features(path, model):
    im = Image.open(path).convert("RGB")
    return {"person": person_score(im, model), **drawing(im)}


def rank(f):
    if f["person"] > PERSON:
        return 2  # model
    if f["pure"] > 0.6 and f["flat"] > 0.75:
        return 0 if f["black"] < 0.3 else 0.5  # illustration (white background first)
    return 1  # the item alone (photo)


def main(model):
    root = Path(".")
    manifest = json.loads((root / "manifest.json").read_text())
    cache_p = root / "order-cache.json"
    cache = json.loads(cache_p.read_text()) if cache_p.exists() else {}
    new_cache, order = {}, {}
    looked = 0
    for k, n in sorted(manifest.items()):
        n = int(n)
        if n < 2:
            continue
        feats = []
        for i in range(1, n + 1):
            name = f"{k}_{i}.jpg"
            p = root / "img" / name
            try:
                h = hashlib.md5(p.read_bytes()).hexdigest()
            except FileNotFoundError:
                feats.append({"person": 1.0, "flat": 0, "pure": 0, "black": 0})
                continue
            c = cache.get(name)
            if not c or c.get("h") != h:
                c = {"h": h, **features(p, model)}
                looked += 1
            new_cache[name] = c
            feats.append(c)
        o = [i + 1 for i in sorted(range(n), key=lambda i: (rank(feats[i]), i))]
        if o != list(range(1, n + 1)):
            order[k] = o
    (root / "order.json").write_text(json.dumps(order, separators=(",", ":"), sort_keys=True))
    rounded = {k: {kk: (round(v, 3) if isinstance(v, float) else v) for kk, v in c.items()} for k, c in new_cache.items()}
    cache_p.write_text(json.dumps(rounded, separators=(",", ":"), sort_keys=True))
    print(f"looked at {looked} new photos; {len(order)} items open on another photo")


if __name__ == "__main__":
    main(sys.argv[1])
