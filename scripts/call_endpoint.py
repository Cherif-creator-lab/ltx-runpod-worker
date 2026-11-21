"""Appelle l'endpoint RunPod depuis ton PC ou depuis le Studio Video (cote serveur).

Usage :
  python scripts/call_endpoint.py image.png "le prompt..." --seconds 6 --out clip.mp4
  python scripts/call_endpoint.py image.png "le prompt..." --end-image fin.png
  python scripts/call_endpoint.py --health

Variables d'environnement (fichier .env, jamais dans le code du navigateur) :
  RUNPOD_API_KEY       cle API RunPod (Credentials > API Keys)
  RUNPOD_ENDPOINT_ID   identifiant de l'endpoint (page de l'endpoint)
"""

from __future__ import annotations

import argparse
import base64
import os
import sys
import time
from pathlib import Path

import requests

API = "https://api.runpod.ai/v2"
POLL_SECONDS = 5
QUEUE_WARNING_SECONDS = 600


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def b64(path: str) -> str:
    return base64.b64encode(Path(path).read_bytes()).decode("ascii")


def run_job(payload: dict, api_key: str, endpoint_id: str, timeout: int = 3600) -> dict:
    headers = {"Authorization": f"Bearer {api_key}"}
    response = requests.post(f"{API}/{endpoint_id}/run", json={"input": payload},
                             headers=headers, timeout=60)
    response.raise_for_status()
    job_id = response.json()["id"]
    print(f"Job envoye : {job_id}")

    started = time.time()
    warned = False
    while time.time() - started < timeout:
        status = requests.get(f"{API}/{endpoint_id}/status/{job_id}",
                              headers=headers, timeout=60).json()
        state = status.get("status")
        if state == "COMPLETED":
            return status.get("output") or {}
        if state in ("FAILED", "CANCELLED", "TIMED_OUT"):
            raise RuntimeError(f"Job {state} : {status.get('error') or status.get('output')}")
        if state == "IN_QUEUE" and not warned and time.time() - started > QUEUE_WARNING_SECONDS:
            print("Toujours en file d'attente apres 10 min : aucun GPU libre pour l'instant.")
            warned = True
        time.sleep(POLL_SECONDS)
    raise TimeoutError("Pas de resultat dans le temps imparti.")


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("image", nargs="?")
    parser.add_argument("prompt", nargs="?")
    parser.add_argument("--end-image")
    parser.add_argument("--seconds", type=int, default=6)
    parser.add_argument("--width", type=int, default=704)
    parser.add_argument("--height", type=int, default=1280)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--out", default="clip.mp4")
    parser.add_argument("--health", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("RUNPOD_API_KEY")
    endpoint_id = os.environ.get("RUNPOD_ENDPOINT_ID")
    if not api_key or not endpoint_id:
        print("Definir RUNPOD_API_KEY et RUNPOD_ENDPOINT_ID dans .env")
        return 1

    if args.health:
        print(run_job({"action": "health"}, api_key, endpoint_id))
        return 0
    if not args.image or not args.prompt:
        parser.error("image et prompt sont obligatoires")

    payload = {
        "image": b64(args.image),
        "prompt": args.prompt,
        "seconds": args.seconds,
        "width": args.width,
        "height": args.height,
    }
    if args.end_image:
        payload["end_image"] = b64(args.end_image)
    if args.seed is not None:
        payload["seed"] = args.seed

    output = run_job(payload, api_key, endpoint_id)
    if "error" in output:
        print(f"Erreur du worker : {output['error']}")
        return 1
    if "video_base64" in output:
        Path(args.out).write_bytes(base64.b64decode(output["video_base64"]))
        print(f"Video enregistree : {args.out}")
    elif "video_url" in output:
        Path(args.out).write_bytes(requests.get(output["video_url"], timeout=300).content)
        print(f"Video telechargee : {args.out}")
    info = {k: v for k, v in output.items() if k not in ("video_base64",)}
    print(info)
    return 0


if __name__ == "__main__":
    sys.exit(main())
