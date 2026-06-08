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


def load_departments(path: Path) -> List[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list):
        raise ValueError("Expected a JSON array of departments")
    return data


def save_departments(path: Path, departments: Sequence[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        json.dump(list(departments), f, ensure_ascii=False, indent=2)


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
        "--departments-json",
        help="Path to departments JSON file",
        default="departments.json",
    )
    parser.add_argument(
        "--model",
        help="Model/deployment to use",
        default=AZURE_OPENAI_DEPLOYMENT,
    )
    parser.add_argument(
        "--output-json",
        help="Output JSON file (default: departments_with_images.json next to input)",
        default="departments_with_images.json",
    )
    args = parser.parse_args()

    departments_path = Path(args.departments_json)
    if not departments_path.exists():
        raise FileNotFoundError(f"Departments JSON not found: {departments_path}")

    departments = load_departments(departments_path)

    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(),
        AZURE_OPENAI_SCOPE,
    )
    client = AzureOpenAI(
        azure_endpoint=AZURE_OPENAI_ENDPOINT,
        api_version=AZURE_OPENAI_API_VERSION,
        azure_ad_token_provider=token_provider,
    )

    for _, department in enumerate(departments):
        name = department.get("name")
        if not name:
            department["image_url"] = None
            continue

        base_name = sanitize_filename(name)

        print(f"Generating image for department: {name}")
        response = client.images.generate(
            model=args.model,
            prompt=name,
            size="auto",
        )
        image_b64 = response.data[0].b64_json

        department["image_url"] = write_image(image_b64, base_name)

    save_departments(Path(args.output_json), departments)


if __name__ == "__main__":
    main()
