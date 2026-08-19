"""Generate per-product photos for the 8 clone envs via TRAPI (gpt-image-1).

Driven by a worklist JSON (built from the 8-env image audit): one entry per product with
a spec-faithful prompt, a staging path, post-processing mode and the final destination(s).

  python scripts/gen_env_product_images.py WORKLIST.json                 # generate + wire all
  python scripts/gen_env_product_images.py WORKLIST.json --defer stockx,instacart
                                          # generate everything, but do NOT copy finals for
                                          # the deferred envs (e.g. mid-remeasure)
  python scripts/gen_env_product_images.py WORKLIST.json --wire-only     # no API calls: copy
                                          # already-staged files to finals (use to wire the
                                          # deferred envs after the run finishes)

Resumable: an entry whose staging PNG exists is not regenerated. Region-pooled at
2 concurrent per region (TRAPI image rate limit), retries on 429/503 with backoff.

Post modes: jpeg (RGB JPEG q90) · png (as-is) · nike2to1 (alpha-trim the transparent
cutout, pad to a centered 2:1 landscape canvas — the nike card <img> is h-36 w-64).
"""
import argparse, asyncio, base64, io, json, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agentarena.llm_client import create_client, _trapi_base_url, TRAPI_DEPLOY

DEP = TRAPI_DEPLOY.get("gpt-image-1", "gpt-image-1")
REGIONS = ["gcr/shared", "msraif/shared", "redmond/interactive"]
PER_REGION = 2


def _post(entry: dict) -> None:
    """staging PNG -> final file(s) per the entry's post mode."""
    from PIL import Image
    src = Path(entry["staging"])
    img = Image.open(src)
    outs = [Path(f) for f in entry["finals"]]
    for o in outs:
        o.parent.mkdir(parents=True, exist_ok=True)
    mode = entry.get("post", "png")
    if mode == "jpeg":
        rgb = img.convert("RGB")
        for o in outs:
            rgb.save(o, "JPEG", quality=90)
    elif mode == "nike2to1":
        img = img.convert("RGBA")
        box = img.getchannel("A").getbbox() or (0, 0, img.width, img.height)
        cut = img.crop(box)
        # centered exact-2:1 canvas with a small margin (card renders ~256x144)
        w = max(int(cut.width * 1.08), 2 * int(cut.height * 1.08))
        h = w // 2
        canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        canvas.paste(cut, ((w - cut.width) // 2, (h - cut.height) // 2), cut)
        for o in outs:
            canvas.save(o, "PNG")
    else:  # png
        for o in outs:
            img.save(o, "PNG")


async def _gen_one(region: str, entry: dict) -> bool:
    last = ""
    for attempt in range(6):
        try:
            c, _ = create_client(model=DEP, base_url=_trapi_base_url(region))
            kw = {"model": DEP, "prompt": entry["prompt"], "size": entry.get("size", "1024x1024")}
            if entry.get("transparent"):
                kw["background"] = "transparent"
            r = await c.images.generate(**kw)
            await c.close()
            staging = Path(entry["staging"])
            staging.parent.mkdir(parents=True, exist_ok=True)
            staging.write_bytes(base64.b64decode(r.data[0].b64_json))
            return True
        except Exception as e:
            last = str(e)[:90]
            if "429" in last or "503" in last or "Rate" in last:
                await asyncio.sleep(4 * (attempt + 1))
            else:
                await asyncio.sleep(2)
    print(f"  FAIL {entry['env']}/{entry['sku']}: {last}", flush=True)
    return False


async def _worker(region, q, prog, wire_envs):
    while True:
        try:
            entry = q.get_nowait()
        except asyncio.QueueEmpty:
            return
        ok = Path(entry["staging"]).exists() or await _gen_one(region, entry)
        if ok and entry["env"] in wire_envs:
            try:
                _post(entry)
            except Exception as e:
                ok = False
                print(f"  POST-FAIL {entry['env']}/{entry['sku']}: {e}", flush=True)
        prog["done"] += 1
        prog["ok"] += int(ok)
        if prog["done"] % 10 == 0:
            print(f"  {prog['done']}/{prog['total']} ({prog['ok']} ok) {time.time()-prog['t0']:.0f}s",
                  flush=True)
        q.task_done()


async def main_async(wl, wire_envs, wire_only):
    prog = {"done": 0, "ok": 0, "total": len(wl), "t0": time.time()}
    if wire_only:
        n = 0
        for entry in wl:
            if entry["env"] in wire_envs and Path(entry["staging"]).exists():
                _post(entry)
                n += 1
        print(f"wired {n} staged images for envs {sorted(wire_envs)}", flush=True)
        return
    q = asyncio.Queue()
    for entry in wl:
        q.put_nowait(entry)
    print(f"to process: {len(wl)} images (concurrency {len(REGIONS)*PER_REGION}; "
          f"wiring finals for {sorted(wire_envs)})", flush=True)
    workers = [asyncio.create_task(_worker(REGIONS[w % len(REGIONS)], q, prog, wire_envs))
               for w in range(len(REGIONS) * PER_REGION)]
    await asyncio.gather(*workers)
    print(f"DONE: {prog['ok']}/{prog['total']} ok in {time.time()-prog['t0']:.0f}s", flush=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("worklist")
    ap.add_argument("--defer", default="", help="comma-separated envs: generate but skip wiring")
    ap.add_argument("--only", default="", help="comma-separated envs: limit to these envs")
    ap.add_argument("--wire-only", action="store_true", help="no API calls; copy staged -> finals")
    a = ap.parse_args()
    wl = json.load(open(a.worklist))
    if a.only:
        keep = set(a.only.split(","))
        wl = [w for w in wl if w["env"] in keep]
    envs = {w["env"] for w in wl}
    wire = envs - set(x for x in a.defer.split(",") if x)
    asyncio.run(main_async(wl, wire, a.wire_only))
