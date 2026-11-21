"""Handler RunPod Serverless : image (+ image de fin) + prompt -> clip MP4 LTX-2.5.

Entree attendue (champ "input" de la requete RunPod) :
{
  "image": "<base64 ou data URI>"      (ou "image_url": "https://...")
  "end_image": "<base64>"              (optionnel, ou "end_image_url")
  "prompt": "<texte>",
  "negative_prompt": "<texte>",        (optionnel)
  "seconds": 6 (entier), "width": 704, "height": 1280, "fps": 24, "seed": 12345
}
Requete de sante : {"input": {"action": "health"}}

Sortie : {"video_url": ...} si un stockage S3/R2 est configure, sinon {"video_base64": ...},
plus seconds, frames, width, height, fps, seed, generation_time_s.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import random
import time
import uuid
from pathlib import Path

import requests
import runpod

from workflow_patch import (
    JobParams,
    WorkflowError,
    find_video_outputs,
    frame_count,
    nodes_titled,
    patch_workflow,
    round_to_multiple,
)

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
COMFY_START_TIMEOUT = int(os.environ.get("COMFY_START_TIMEOUT", "900"))
JOB_TIMEOUT = int(os.environ.get("JOB_TIMEOUT", "1740"))
WORKFLOW_DIR = Path(os.environ.get("WORKFLOW_DIR", Path(__file__).with_name("workflows")))
I2V_WORKFLOW = WORKFLOW_DIR / "ltx25_i2v_api.json"
FLF2V_WORKFLOW = WORKFLOW_DIR / "ltx25_flf2v_api.json"
COMFY_OUTPUT_DIR = Path(os.environ.get("COMFY_OUTPUT_DIR", "/comfyui/output"))
COMFY_INPUT_DIR = Path(os.environ.get("COMFY_INPUT_DIR", "/comfyui/input"))
MAX_BASE64_MB = float(os.environ.get("MAX_BASE64_MB", "15"))

LIMITS = {"seconds": (1, 20), "width": (256, 1920), "height": (256, 1920), "fps": (8, 50)}


def log(message: str) -> None:
    print(f"[handler] {message}", flush=True)


# ---------------------------------------------------------------- ComfyUI

def wait_for_comfy() -> None:
    deadline = time.time() + COMFY_START_TIMEOUT
    while time.time() < deadline:
        try:
            if requests.get(f"{COMFY_URL}/system_stats", timeout=5).ok:
                log("ComfyUI est pret.")
                return
        except requests.RequestException:
            pass
        time.sleep(2)
    raise RuntimeError("ComfyUI n'a pas demarre a temps.")


def upload_image(data: bytes, filename: str) -> str:
    files = {"image": (filename, data, "application/octet-stream")}
    response = requests.post(
        f"{COMFY_URL}/upload/image", files=files, data={"overwrite": "true"}, timeout=60
    )
    response.raise_for_status()
    return response.json()["name"]


def queue_prompt(workflow: dict) -> str:
    response = requests.post(
        f"{COMFY_URL}/prompt",
        json={"prompt": workflow, "client_id": str(uuid.uuid4())},
        timeout=60,
    )
    if response.status_code != 200:
        try:
            details = response.json()
        except ValueError:
            details = response.text
        raise RuntimeError(f"ComfyUI a refuse le workflow : {json.dumps(details)[:2000]}")
    return response.json()["prompt_id"]


def wait_for_result(prompt_id: str, job: dict) -> dict:
    deadline = time.time() + JOB_TIMEOUT
    last_progress = 0.0
    while time.time() < deadline:
        response = requests.get(f"{COMFY_URL}/history/{prompt_id}", timeout=30)
        response.raise_for_status()
        entry = response.json().get(prompt_id)
        if entry:
            status = entry.get("status", {})
            if status.get("status_str") == "error":
                messages = [m for m in status.get("messages", []) if m and m[0] == "execution_error"]
                detail = messages[-1][1].get("exception_message", "") if messages else ""
                raise RuntimeError(f"Erreur pendant la generation : {detail}".strip())
            if status.get("completed") or entry.get("outputs"):
                return entry
        if time.time() - last_progress > 30:
            runpod.serverless.progress_update(job, "Generation en cours...")
            last_progress = time.time()
        time.sleep(2)
    raise TimeoutError("La generation a depasse le temps maximum.")


def read_output_file(item: dict) -> bytes:
    params = {
        "filename": item["filename"],
        "subfolder": item.get("subfolder", ""),
        "type": item.get("type", "output"),
    }
    response = requests.get(f"{COMFY_URL}/view", params=params, timeout=300)
    response.raise_for_status()
    return response.content


def cleanup(paths: list[Path]) -> None:
    for path in paths:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


# ---------------------------------------------------------------- Entrees

def decode_image(value: str, field_name: str) -> bytes:
    if "," in value and value.strip().startswith("data:"):
        value = value.split(",", 1)[1]
    try:
        return base64.b64decode(value, validate=False)
    except (binascii.Error, ValueError) as error:
        raise WorkflowError(f"'{field_name}' n'est pas un base64 valide.") from error


def fetch_image(job_input: dict, key: str) -> bytes | None:
    if job_input.get(key):
        return decode_image(job_input[key], key)
    url = job_input.get(f"{key}_url")
    if url:
        response = requests.get(url, timeout=60)
        response.raise_for_status()
        return response.content
    return None


def number(job_input: dict, key: str, default):
    value = job_input.get(key, default)
    try:
        value = float(value)
    except (TypeError, ValueError) as error:
        raise WorkflowError(f"'{key}' doit etre un nombre.") from error
    low, high = LIMITS[key]
    if not low <= value <= high:
        raise WorkflowError(f"'{key}' doit etre entre {low} et {high}.")
    return value


# ---------------------------------------------------------------- Sortie

def s3_configured() -> bool:
    return all(os.environ.get(k) for k in (
        "BUCKET_ENDPOINT_URL", "BUCKET_ACCESS_KEY_ID", "BUCKET_SECRET_ACCESS_KEY", "BUCKET_NAME"
    ))


def upload_to_s3(data: bytes, key: str) -> str:
    import boto3

    client = boto3.client(
        "s3",
        endpoint_url=os.environ["BUCKET_ENDPOINT_URL"],
        aws_access_key_id=os.environ["BUCKET_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["BUCKET_SECRET_ACCESS_KEY"],
        region_name=os.environ.get("BUCKET_REGION", "auto"),
    )
    bucket = os.environ["BUCKET_NAME"]
    client.put_object(Bucket=bucket, Key=key, Body=data, ContentType="video/mp4")
    public_base = os.environ.get("BUCKET_PUBLIC_URL")
    if public_base:
        return f"{public_base.rstrip('/')}/{key}"
    expires = int(os.environ.get("BUCKET_URL_EXPIRES", "604800"))
    return client.generate_presigned_url(
        "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires
    )


# ---------------------------------------------------------------- Handler

def health() -> dict:
    models_root = Path(os.environ.get("MODELS_DIR", "/runpod-volume/models"))
    models = sorted(str(p.relative_to(models_root)) for p in models_root.rglob("*.safetensors")
                    if ".download_tmp" not in p.parts) if models_root.exists() else []
    local_root = Path(os.environ.get("LOCAL_MODELS_DIR", "/models-local"))
    local = sorted(str(p.relative_to(local_root)) for p in local_root.rglob("*.safetensors")) \
        if local_root.exists() else []
    return {
        "status": "ok",
        "local_models": local,
        "workflows": {"i2v": I2V_WORKFLOW.exists(), "flf2v": FLF2V_WORKFLOW.exists()},
        "models": models,
        "storage": "s3" if s3_configured() else "base64",
    }


def handler(job: dict) -> dict:
    job_input = job.get("input") or {}
    if job_input.get("action") == "health":
        return health()

    temp_files: list[Path] = []
    started = time.time()
    try:
        prompt = str(job_input.get("prompt", "")).strip()
        if not prompt:
            raise WorkflowError("Le champ 'prompt' est obligatoire.")

        start_image = fetch_image(job_input, "image")
        if not start_image:
            raise WorkflowError("Le champ 'image' (ou 'image_url') est obligatoire.")
        end_image = fetch_image(job_input, "end_image")

        # Le workflow LTX-2.5 prend une duree en secondes entieres.
        seconds = int(round(number(job_input, "seconds", 6)))
        fps = int(number(job_input, "fps", 24))
        width = round_to_multiple(int(number(job_input, "width", 704)))
        height = round_to_multiple(int(number(job_input, "height", 1280)))
        frames = frame_count(seconds, fps)
        seed = int(job_input.get("seed", random.randint(0, 2**31 - 1)))

        workflow_path = FLF2V_WORKFLOW if end_image else I2V_WORKFLOW
        if not workflow_path.exists():
            raise WorkflowError(f"Workflow manquant dans l'image : {workflow_path.name}")
        workflow = json.loads(workflow_path.read_text(encoding="utf-8"))

        job_tag = job.get("id", uuid.uuid4().hex)[:12]
        image_name = upload_image(start_image, f"start_{job_tag}.png")
        temp_files.append(COMFY_INPUT_DIR / image_name)
        end_name = None
        if end_image:
            end_name = upload_image(end_image, f"end_{job_tag}.png")
            temp_files.append(COMFY_INPUT_DIR / end_name)

        params = JobParams(
            prompt=prompt,
            negative_prompt=job_input.get("negative_prompt"),
            image_name=image_name,
            end_image_name=end_name,
            width=width,
            height=height,
            seconds=seconds,
            fps=fps,
            seed=seed,
        )
        patched, report = patch_workflow(workflow, params)
        log(f"Noeuds modifies : {report.touched}")

        prompt_id = queue_prompt(patched)
        entry = wait_for_result(prompt_id, job)

        videos = find_video_outputs(entry, nodes_titled(patched, "OUTPUT_VIDEO"))
        if not videos:
            raise RuntimeError("Aucune video trouvee dans le resultat de ComfyUI.")
        video_item = videos[0]
        video_bytes = read_output_file(video_item)
        temp_files.append(COMFY_OUTPUT_DIR / video_item.get("subfolder", "") / video_item["filename"])

        result = {
            "seconds": seconds,
            "frames": frames,
            "width": width,
            "height": height,
            "fps": fps,
            "seed": seed,
            "generation_time_s": round(time.time() - started, 1),
            "size_mb": round(len(video_bytes) / 1e6, 2),
        }
        if s3_configured():
            result["video_url"] = upload_to_s3(video_bytes, f"ltx/{job_tag}.mp4")
        else:
            if result["size_mb"] > MAX_BASE64_MB:
                raise RuntimeError(
                    f"Video de {result['size_mb']} Mo, trop lourde pour etre renvoyee en base64. "
                    "Configurer un stockage S3/R2 (variables BUCKET_...)."
                )
            result["video_base64"] = base64.b64encode(video_bytes).decode("ascii")
        return result

    except (WorkflowError, TimeoutError, RuntimeError, requests.RequestException) as error:
        log(f"Erreur : {error}")
        return {"error": str(error)}
    finally:
        cleanup(temp_files)


if __name__ == "__main__":
    wait_for_comfy()
    runpod.serverless.start({"handler": handler})
