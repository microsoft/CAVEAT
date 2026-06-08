import argparse
import base64
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Sequence

from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider


IMAGE_WIDTH = 512
IMAGE_HEIGHT = 512

AZURE_OPENAI_ENDPOINT = "https://trapi.research.microsoft.com/gcr/shared"
AZURE_OPENAI_API_VERSION = "2025-04-01-preview"
AZURE_OPENAI_DEPLOYMENT = "gpt-image-1"
AZURE_OPENAI_SCOPE = "api://trapi/.default"


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


def write_image(image_b64: str, base_name: str) -> str:
    Path("images").mkdir(parents=True, exist_ok=True)
    file_name = f"{base_name}.png"
    out_path = Path("images") / file_name
    out_path.write_bytes(base64.b64decode(image_b64))
    return str(out_path)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate product images using Azure OpenAI"
    )
    parser.add_argument(
        "--products-json",
        help="Path to products JSON file",
        default="products.json",
    )
    parser.add_argument(
        "--model",
        help="Model/deployment to use",
        default=AZURE_OPENAI_DEPLOYMENT,
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

    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(),
        AZURE_OPENAI_SCOPE,
    )
    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_version=AZURE_OPENAI_API_VERSION,
        azure_ad_token_provider=token_provider,
    )

    for _, product in enumerate(products):
        title = product.get("title")
        if not title:
            product["image_path"] = None
            continue

        base_name = sanitize_filename(title)

        Path("images").mkdir(parents=True, exist_ok=True)
        file_name = f"{base_name}.png"
        out_path = Path("images") / file_name
        if out_path.exists():
            continue

        print(f"Generating image for product: {title}")
        response = client.images.generate(
            model=args.model,
            prompt=title,
            size="auto",
        )
        image_b64 = response.data[0].b64_json

        product["image_path"] = write_image(image_b64, base_name)

    save_products(Path(args.output_json), products)


if __name__ == "__main__":
    main()
