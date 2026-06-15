"""Generate category-appropriate generic product photos for the new scenarios (gpt-image-1 over
TRAPI), with long backoff for the intermittent 503. Writes 4 variants per category into the Amazon
server's images dir; updates each scenario's catalog.json to spread the variants across products.
Idempotent + resumable: skips files that already exist and are > 50KB (a real photo, not the 3KB
placeholder). Run in the background."""
import json
import time
from pathlib import Path
from agentarena.llm_client import generate_image_sync
from agentarena.benchmark import serialize

IMG = Path("agentarena/envs/amazon/server/backend/images")
N_VARIANTS = 4
DESCRIPTORS = {
    "office_chair": ["ergonomic mesh office chair", "high-back executive office chair",
                     "modern ergonomic task chair", "mesh-back computer desk chair"],
    "mattress": ["queen memory-foam mattress", "queen hybrid mattress, cross-section visible",
                 "rolled bed-in-a-box queen mattress", "thick queen foam mattress on a plain base"],
    "backpack": ["travel daypack backpack", "minimalist commuter backpack",
                 "water-resistant laptop backpack", "lightweight hiking daypack"],
    "tent": ["three-person dome backpacking tent", "freestanding camping tent with rainfly",
             "lightweight backpacking tent", "two-door dome tent"],
}


def prompt(desc):
    return (f"Professional e-commerce studio product photograph of a {desc}, centered on a plain "
            f"white seamless background, soft even lighting, high detail, no text, no logos, "
            f"no watermark, no people.")


def gen_one(out: Path, desc: str, tries=8) -> bool:
    if out.exists() and out.stat().st_size > 50_000:
        return True
    for attempt in range(tries):
        try:
            generate_image_sync(prompt(desc), image_model="gpt-image-1", size="1024x1024",
                                out_path=str(out))
            print(f"  wrote {out.name} ({out.stat().st_size//1024}KB)", flush=True)
            return True
        except Exception as e:
            msg = str(e)[:80]
            wait = min(30, 4 * (attempt + 1))
            print(f"  {out.name} attempt {attempt+1} failed: {msg} — retry in {wait}s", flush=True)
            time.sleep(wait)
    return False


def main():
    for sid, descs in DESCRIPTORS.items():
        print(f"[{sid}]", flush=True)
        files = []
        for k in range(N_VARIANTS):
            f = IMG / f"{sid}-generic-{k}.png"
            if gen_one(f, descs[k]):
                files.append(f.name)
        if not files:
            print(f"  [{sid}] no images generated; keeping placeholder", flush=True)
            continue
        # also overwrite the single fallback name with variant 0
        if (IMG / f"{sid}-generic-0.png").exists():
            (IMG / f"{sid}-generic.png").write_bytes((IMG / f"{sid}-generic-0.png").read_bytes())
        # spread variants across the catalog products (round-robin) for realism
        cj = serialize.load_catalog_json(sid)
        for i, p in enumerate(cj["products"]):
            p["image"] = files[i % len(files)]
        serialize.scenario_dir(sid).joinpath("catalog.json").write_text(json.dumps(cj, indent=2))
        print(f"  [{sid}] assigned {len(files)} photos across {len(cj['products'])} products", flush=True)
    print("DONE", flush=True)


if __name__ == "__main__":
    main()
