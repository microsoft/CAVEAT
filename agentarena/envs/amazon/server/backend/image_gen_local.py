import argparse
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence
import torch
from diffusers import DiffusionPipeline

IMAGE_WIDTH = 512
IMAGE_HEIGHT = 512


def sanitize_filename(text: str, max_length: int = 80) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", text.strip().lower())
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    if not cleaned:
        cleaned = "image"
    return cleaned[:max_length]


def load_products(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of products")
    return data


def save_products(path: Path, products: Sequence[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(list(products), f, ensure_ascii=False, indent=2)


def load_pipeline(model_path: str, device: str):
    if torch is None or DiffusionPipeline is None:
        raise RuntimeError(
            "Missing dependencies. Install torch, diffusers, transformers, accelerate, safetensors, pillow."
        )

    dtype = torch.float16 if device == "cuda" else torch.float32
    pipe = DiffusionPipeline.from_pretrained(model_path, torch_dtype=dtype)
    pipe = pipe.to(device)
    return pipe


def generate_image(
    pipe,
    prompt: str,
    base_name: str,
    device: str,
) -> str:
    output_path = ""

    if device == "mps":
        with torch.autocast("mps"):
            images = pipe(prompt, height=IMAGE_HEIGHT, width=IMAGE_WIDTH).images
    else:
        images = pipe(prompt, height=IMAGE_HEIGHT, width=IMAGE_WIDTH).images

    for _, img in enumerate(images):
        file_name = f"{base_name}.png"
        out_path = Path("images") / file_name
        img.save(out_path)
        output_path = str(out_path)
        break  # Only generate one image per prompt

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate product images from a local Hugging Face model"
    )
    parser.add_argument(
        "--products-json",
        help="Path to products JSON file",
        default="products.json",
    )
    parser.add_argument(
        "--model-path",
        help="Local path or model id for the image model",
        default="Qwen/Qwen-Image",
    )
    parser.add_argument(
        "--output-json",
        help="Output JSON file (default: products_with_images.json next to input)",
        default="products_with_images.json",
    )
    args = parser.parse_args()

    products_path = Path(args.products_json)
    if not products_path.exists():
        raise FileNotFoundError(f"Products JSON not found: {products_path}")

    products = load_products(products_path)

    # try:
    #     device = "cuda" if torch and torch.cuda.is_available() else "cpu"
    # except Exception:
    #     device = "cpu"

    try:
        print("Checking for MPS device...")
        device = "mps" if torch.backends.mps.is_available() else "cpu"
        x = torch.ones(1, device=device)
        print(x)
    except Exception:
        device = "cpu"

    pipe = load_pipeline(args.model_path, device)

    for idx, product in enumerate(products):
        title = product.get("title")
        if not title:
            product["image_path"] = None
            continue

        base_name = sanitize_filename(title)
        image_path = generate_image(
            pipe=pipe,
            prompt=title,
            base_name=f"{idx + 1:04d}_{base_name}",
            device=device,
        )

        product["image_path"] = image_path

    save_products(Path(args.output_json), products)


if __name__ == "__main__":
    main()
