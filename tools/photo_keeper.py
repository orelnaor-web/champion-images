#!/usr/bin/env python3
"""Photo keeper (Orel 12.11.2026: "אי אפשר להישען על האחסון של האתר שלהם ... אני לא רוצה להיות מושפע ממחיקה או שינוי שלהם").

Every product photo the Click apps show must live in OUR gallery (this repo, GitHub Pages), never only on someone
else's site. Runs daily on Orel's PC (Windows task "Champion Photo Keeper", tools/install_photo_keeper.ps1):

 1. Reads the three SBN sitemaps (timberland.co.il, nautica.co.il, emporium.co.il) with headless Chrome — the sites
    turn away plain server requests, so the Click server can no longer read them itself (12.11.2026).
 2. Stock items (Click Citrix, derived stock-<brand> packs) that have no photo yet and appear on a site (file name =
    our item code) get a new ProductImage row (Click Control).
 3. Every photo link that is not ours yet (those sites, assets.timberland.eu, shopify, ...) is downloaded, made
    smaller (max 900px, JPEG 76) and saved under b/<site>/ here; pushed; the rows (urls + hidden_urls) and the agents'
    personal choices (Click Citrix DataPack img-main) are switched to our copy. Nothing is deleted: the original link of
    every copy stays in b/source-map.json (our url -> original url).

A photo that is already gone from the site (404) cannot be copied; its row keeps the old link (listed in the log).

Usage:  python tools/photo_keeper.py            (from anywhere; full run)
        python tools/photo_keeper.py --dry      (no push, no writes to the apps)
Needs: Python + Pillow, git with push rights to this repo, `npx base44` logged in (click-control checkout), Chrome.
"""
import concurrent.futures as cf
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from PIL import Image

REPO = Path(__file__).resolve().parent.parent
PAGES = "https://orelnaor-web.github.io/champion-images"
CONTROL_DIR = Path(r"C:\Users\oreln\repos\click-control")       # any checkout with the base44 CLI
TOOLS = Path(r"C:\Users\oreln\ChampionTools")
DENO_DIR = TOOLS / "deno-cache"   # own Deno cache: the default one broke on 9.11 (V8 "ReadSingleBytecodeData" panic)
LOG = TOOLS / "logs" / "photo-keeper.log"
CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
APP_CONTROL, APP_CITRIX = "6adee8dbfd8ec98bb916290c", "6adee746df10b3e0dd6ff5e8"
SITES = [("timberland", "https://www.timberland.co.il/sitemap.xml"), ("nautica", "https://www.nautica.co.il/sitemap.xml"),
         ("emporium", "https://www.emporium.co.il/sitemap.xml")]
PREF = {"nautica": ["nautica", "emporium", "timberland"], "timberland": ["timberland", "emporium", "nautica"],
        "gregnorman": ["emporium", "nautica", "timberland"], "champion": ["emporium", "nautica", "timberland"]}
FOLDER = {"www.timberland.co.il": "b/tbl", "assets.timberland.eu": "b/tbleu", "cdn.shopify.com": "b/shop",
          "www.nautica.co.il": "b/nau", "www.emporium.co.il": "b/emp"}
OURS = re.compile(r"^https://(orelnaor-web\.github\.io|base44\.app|[a-z0-9-]+\.base44\.app|media\.base44\.com)/", re.I)
MAXSIDE, QUALITY = 900, 76
DRY = "--dry" in sys.argv


def log(*a):
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S} " + " ".join(str(x) for x in a)
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def https(u):
    return re.sub(r"^http://", "https://", str(u or ""))


# ---------- Base44 (read / write through the CLI, as the logged-in owner) ----------
def b44(app_id, script, timeout=3600):
    DENO_DIR.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "BASE44_APP_ID": app_id, "DENO_DIR": str(DENO_DIR)}
    r = subprocess.run("npx base44 exec --privileged", input=script, cwd=CONTROL_DIR, env=env, shell=True,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    out = r.stdout or ""
    if "KEEPER_OK" not in out:
        raise RuntimeError(f"base44 exec failed ({r.returncode}): {(r.stderr or out)[-400:]}")
    return out


def b44_json(app_id, body):
    """body = JS that sets `result`; returns it as Python data (through a temp file)."""
    with tempfile.TemporaryDirectory() as d:
        p = Path(d, "out.json").as_posix()
        b44(app_id, "const items=(r)=>Array.isArray(r)?r:r?.items??[];\nlet result=null;\n" + body +
            f"\nawait Deno.writeTextFile({json.dumps(p)}, JSON.stringify(result));\nconsole.log('KEEPER_OK');\n")
        return json.loads(Path(p).read_text(encoding="utf-8"))


def load_images():
    return b44_json(APP_CONTROL, "result=[];for(let s=0;;s+=200){const p=items(await base44.entities.ProductImage.list('created_date',200,s));result.push(...p);if(p.length<200)break;}")


def load_stock():
    js = """
result={};
const packs=items(await base44.entities.DataPack.filter({kind:'derived'},'-created_date',100));
const find=o=>{if(o&&!Array.isArray(o)&&Array.isArray(o.items))return o.items;if(o&&typeof o==='object')for(const v of Object.values(o)){const r=find(v);if(r)return r}};
for(const b of ['nautica','timberland','gregnorman','champion']){
 const p=packs.find(x=>x.key==='stock-'+b); if(!p) continue;
 const ch=items(await base44.entities.DataPackChunk.filter({pack_id:p.id},'idx',5000)).sort((a,b)=>a.idx-b.idx);
 const bin=Uint8Array.from(atob(ch.map(c=>c.b64).join('')),c=>c.charCodeAt(0));
 const text=await new Response(new Blob([bin]).stream().pipeThrough(new DecompressionStream('gzip'))).text();
 result[b]=(find(JSON.parse(text))||[]).map(x=>x.code);
}"""
    return b44_json(APP_CITRIX, js)


# ---------- the sites ----------
def sitemap_urls(url):
    with tempfile.TemporaryDirectory() as prof:
        r = subprocess.run([CHROME, "--headless=new", "--disable-gpu", f"--user-data-dir={prof}", "--dump-dom", url],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
    return sorted(set(re.findall(r"<image:loc>\s*([^<\s]+)\s*</image:loc>", r.stdout or "")))


def site_index():
    idx = {}
    for site, url in SITES:
        try:
            urls = sitemap_urls(url)
        except Exception as e:  # noqa: BLE001
            log("sitemap failed", site, e)
            continue
        log("sitemap", site, len(urls), "photos")
        for u in urls:
            fn = u.split("/")[-1]
            stem = re.sub(r"-\d{10,}\.(jpe?g|png|webp)$", "", fn, flags=re.I)
            if stem == fn:
                continue
            m = re.match(r"^(.*?)(?:_(\d+))?$", stem)
            o = https(re.sub(r"/cache/[0-9a-f]{20,}/", "/", u.split("?")[0]))
            lst = idx.setdefault(norm(m.group(1)), {}).setdefault(site, [])
            if all(x[1] != o for x in lst):
                lst.append((int(m.group(2) or 0), o))
    return idx


# ---------- copies ----------
def target(u):
    host = re.match(r"https?://([^/]+)", u).group(1).lower()
    folder = FOLDER.get(host, "b/other")
    base = re.sub(r"[^A-Za-z0-9._-]", "_", u.split("?")[0].rstrip("/").split("/")[-1])
    base = re.sub(r"\.(jpe?g|png|webp|gif)$", "", base, flags=re.I)[:90]
    rel = f"{folder}/{base}-{hashlib.sha1(u.encode()).hexdigest()[:6]}.jpg"
    return REPO / rel, f"{PAGES}/{rel}"


def copy_one(u):
    path, ours = target(u)
    if not path.exists():
        req = urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"})
        im = Image.open(io.BytesIO(urllib.request.urlopen(req, timeout=40).read()))
        im.load()
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        if max(im.size) > MAXSIDE:
            im.thumbnail((MAXSIDE, MAXSIDE), Image.LANCZOS)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part")
        im.save(tmp, "JPEG", quality=QUALITY, optimize=True, progressive=True)
        os.replace(tmp, path)
    return ours


def copy_all(urls):
    done, fails, lock = {}, {}, threading.Lock()
    with cf.ThreadPoolExecutor(8) as ex:
        futs = {ex.submit(copy_one, u): u for u in urls}
        for i, f in enumerate(cf.as_completed(futs), 1):
            u = futs[f]
            try:
                ours = f.result()
                with lock:
                    done[u] = ours
            except Exception as e:  # noqa: BLE001
                fails[u] = str(e)[:120]
            if i % 1000 == 0:
                log("copied", i, "of", len(urls))
    return done, fails


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, encoding="utf-8", check=check).stdout


def publish(new_map):
    """Commit b/ in batches and push; the source map keeps every original link."""
    smap_path = REPO / "b" / "source-map.json"
    smap = json.loads(smap_path.read_text(encoding="utf-8")) if smap_path.exists() else {}
    smap.update({ours: orig for orig, ours in new_map.items()})
    smap_path.parent.mkdir(parents=True, exist_ok=True)
    smap_path.write_text(json.dumps(smap, ensure_ascii=False, indent=0, sort_keys=True), encoding="utf-8")
    git("pull", "--rebase", "-q", "origin", "main", check=False)
    pending = [l[3:] for l in git("status", "--porcelain", "-uall", "b").splitlines() if l[3:].endswith(".jpg")]
    for i in range(0, len(pending), 1500):
        subprocess.run(["git", "add", "--pathspec-from-file=-"], cwd=REPO, input="\n".join(pending[i:i + 1500]),
                       text=True, encoding="utf-8", check=True)
        git("commit", "-q", "-m", f"תמונות מוצר — עותק שלנו ({i + 1}-{min(i + 1500, len(pending))} מתוך {len(pending)})")
        git("push", "-q", "origin", "HEAD:main")
        log("pushed", min(i + 1500, len(pending)), "of", len(pending))
    git("add", "b/source-map.json")
    if git("diff", "--cached", "--name-only"):
        git("commit", "-q", "-m", "תמונות מוצר — מפת המקורות")
        git("push", "-q", "origin", "HEAD:main")
    return pending


def wait_live(urls, minutes=20):
    """GitHub Pages publishes a few minutes after the push: wait until a sample answers 200."""
    sample = urls[-5:] + urls[:5]
    end = time.time() + minutes * 60
    while time.time() < end:
        ok = 0
        for u in sample:
            try:
                ok += urllib.request.urlopen(urllib.request.Request(u, method="HEAD"), timeout=20).status == 200
            except Exception:  # noqa: BLE001
                pass
        if ok == len(sample):
            return True
        time.sleep(30)
    return False


# ---------- writes to the apps ----------
SWAP_JS = """
const MAP=JSON.parse(await Deno.readTextFile(%(map)s));const NEW=JSON.parse(await Deno.readTextFile(%(new)s));
const fix=(u)=>MAP[String(u||'').replace(/^http:\\/\\//,'https://')]||u;
const PI=base44.entities.ProductImage;const now=new Date().toISOString();
const sleep=(ms)=>new Promise(r=>setTimeout(r,ms));
const retry=async(fn)=>{for(let a=0;;a++){try{return await fn()}catch(e){if(a<5&&/rate|429|50\\d/i.test(String(e?.message||e))){await sleep(5000*(a+1));continue}throw e}}};
const all=[];for(let s=0;;s+=200){const p=items(await PI.list('created_date',200,s));all.push(...p);if(p.length<200)break;}
const byKey=new Map(all.map(r=>[r.key,r]));
let created=0,changed=0,failed=0;
for(const n of NEW){const r=byKey.get(n.key);if(r&&(r.urls||[]).length)continue;
 try{if(r)await retry(()=>PI.update(r.id,{urls:n.urls.map(fix),source:n.source,updated_at:now}));else await retry(()=>PI.create({key:n.key,code:n.code,brand:n.brand,source:n.source,urls:n.urls.map(fix),updated_at:now}));created++;}catch(e){failed++}
 await sleep(420);}
for(const r of all){const urls=(r.urls||[]).map(fix),hid=(r.hidden_urls||[]).map(fix);
 if(urls.every((u,i)=>u===r.urls[i])&&hid.every((u,i)=>u===(r.hidden_urls||[])[i]))continue;
 try{await retry(()=>PI.update(r.id,{urls,...(r.hidden_urls?{hidden_urls:hid}:{}),updated_at:now}));changed++;}catch(e){failed++}
 await sleep(420);}
result={created,changed,failed};
"""

PREFS_JS = """
const MAP=JSON.parse(await Deno.readTextFile(%(map)s));
const fix=(u)=>MAP[String(u||'').replace(/^http:\\/\\//,'https://')]||u;
const deep=(v)=>typeof v==='string'?fix(v):Array.isArray(v)?v.map(deep):v&&typeof v==='object'?Object.fromEntries(Object.entries(v).map(([k,x])=>[k,deep(x)])):v;
let changed=0;for(const p of items(await base44.entities.DataPack.filter({kind:'img-main'},'-updated_date',500))){
 const parts=p.parts||{};const next={...parts,map:deep(parts.map||{}),hide:deep(parts.hide||{})};
 if(JSON.stringify(next)===JSON.stringify(parts))continue;await base44.entities.DataPack.update(p.id,{parts:next});changed++;}
result={changed};
"""


def main():
    log("start", "(dry)" if DRY else "")
    rows = load_images()
    have = {r["key"] for r in rows if r.get("urls")}
    stock = load_stock()
    idx = site_index()
    new = []
    for brand, codes in stock.items():
        for code in dict.fromkeys(codes):
            k = norm(code)
            if k in have or k not in idx:
                continue
            site = next((s for s in PREF.get(brand, []) if s in idx[k]), None)
            if site:
                urls = [u for _, u in sorted(idx[k][site])][:6]
                new.append({"key": k, "code": code, "brand": brand, "source": f"{site}.co.il", "urls": urls})
                have.add(k)
    ext = {https(u) for r in rows for u in (r.get("urls") or []) + (r.get("hidden_urls") or []) if not OURS.match(https(u))}
    ext |= {u for n in new for u in n["urls"]}
    log("items without photo found on the sites:", len(new), "| links to copy:", len(ext))
    done, fails = copy_all(sorted(ext))
    for u, e in list(fails.items())[:30]:
        log("not copied", u, e)
    log("copied ok", len(done), "| not copied", len(fails))
    if DRY:
        log("dry run: nothing pushed or written")
        return
    pushed = publish(done)
    if pushed and not wait_live([f"{PAGES}/{p}" for p in pushed]):
        log("STOP: GitHub Pages did not publish the new photos in time; the apps were not switched (next run retries)")
        return
    with tempfile.TemporaryDirectory() as d:
        mp, np_ = Path(d, "map.json"), Path(d, "new.json")
        mp.write_text(json.dumps(done), encoding="utf-8")
        np_.write_text(json.dumps(new), encoding="utf-8")
        res = b44_json(APP_CONTROL, SWAP_JS % {"map": json.dumps(mp.as_posix()), "new": json.dumps(np_.as_posix())})
        log("Click Control photos:", res)
        res = b44_json(APP_CITRIX, PREFS_JS % {"map": json.dumps(mp.as_posix())})
        log("agents' own photo choices:", res)
    log("done")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        log("FAILED", repr(e)[:500])
        sys.exit(1)
