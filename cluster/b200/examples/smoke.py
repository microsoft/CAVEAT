import json
import os
import pathlib
import urllib.request

import torch

root = pathlib.Path(os.environ["REMOTE_JOB_OUT_DIR"])
message = (root / "source_dir" / "input" / "message.txt").read_text().strip()

request = urllib.request.Request(
    "https://huggingface.co/api/whoami-v2",
    headers={"Authorization": f"Bearer {os.environ['HF_TOKEN']}"},
)
with urllib.request.urlopen(request, timeout=30) as response:
    hf_user = json.load(response)["name"]

result = {
    "gpu_available": torch.cuda.is_available(),
    "gpu_count": torch.cuda.device_count(),
    "hf_authenticated": bool(hf_user),
    "input": message,
}
(root / "result.json").write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result))
