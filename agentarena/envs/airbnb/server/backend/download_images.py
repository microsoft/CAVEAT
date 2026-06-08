"""Download all listing images from Unsplash to local storage."""
import os
import requests
from sqlmodel import Session, select
from backend.database import get_engine, set_db_path
from backend.models import ListingImage

# Store in public/ so vite copies them to dist/ during build
PUBLIC_IMAGES_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "public", "listing-images")
DIST_IMAGES_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist", "listing-images")


def download_images(db_path: str = "./airbnb.db"):
    set_db_path(db_path)
    engine = get_engine()
    os.makedirs(PUBLIC_IMAGES_DIR, exist_ok=True)

    with Session(engine) as session:
        images = session.exec(select(ListingImage)).all()
        print(f"Found {len(images)} images to process")

        for img in images:
            filename = f"{img.id}.jpg"
            filepath = os.path.join(PUBLIC_IMAGES_DIR, filename)
            local_url = f"/listing-images/{filename}"

            if os.path.exists(filepath) and os.path.getsize(filepath) > 0:
                if img.url != local_url:
                    img.url = local_url
                    session.add(img)
                continue

            source_url = img.url
            if source_url.startswith("/listing-images/"):
                print(f"  Skipping image {img.id}: local URL but file missing, will re-seed")
                continue

            try:
                print(f"  Downloading image {img.id}: {source_url[:60]}...")
                resp = requests.get(source_url, timeout=15)
                resp.raise_for_status()
                with open(filepath, "wb") as f:
                    f.write(resp.content)
                img.url = local_url
                session.add(img)
                print(f"    -> Saved as {filename} ({len(resp.content)} bytes)")
            except Exception as e:
                print(f"    -> FAILED: {e}")

        session.commit()
        print("Done! All image URLs updated to local paths.")

    # Also copy to dist/ for immediate use
    os.makedirs(DIST_IMAGES_DIR, exist_ok=True)
    import shutil
    for f in os.listdir(PUBLIC_IMAGES_DIR):
        src = os.path.join(PUBLIC_IMAGES_DIR, f)
        dst = os.path.join(DIST_IMAGES_DIR, f)
        if not os.path.exists(dst):
            shutil.copy2(src, dst)


if __name__ == "__main__":
    download_images()
