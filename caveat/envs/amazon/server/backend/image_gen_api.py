import argparse
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence
from huggingface_hub import InferenceClient


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


def write_image(
    img,
    base_name: str,
) -> str:
    output_path = ""

    file_name = f"{base_name}.png"
    out_path = Path("images") / file_name
    img.save(out_path)
    output_path = str(out_path)

    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate product images from a Hugging Face model via the API"
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

    client = InferenceClient(
        api_key=os.environ["HF_TOKEN"],
    )

    for _, product in enumerate(products):
        title = product.get("title")
        if not title:
            product["image_path"] = None
            continue

        base_name = sanitize_filename(title)

        print(f"Generating image for product: {title}")
        image = client.text_to_image(
            title,
            model=args.model_path,
        )

        product["image_path"] = write_image(image, base_name)

    save_products(Path(args.output_json), products)


if __name__ == "__main__":
    main()
