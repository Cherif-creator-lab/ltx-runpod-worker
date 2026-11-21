"""Telecharge les modeles directement depuis Hugging Face vers le disque local.

Pourquoi : le Global volume RunPod se lit a 40-65 Mo/s (15 min pour 45 Go).
Hugging Face livre bien plus vite, surtout avec le protocole hf_xet et
plusieurs fichiers en parallele. Le volume ne sert plus que de secours.

En cas de succes, ecrit /comfyui/extra_model_paths.yaml vers le disque local
et renvoie 0. En cas d'echec, renvoie 1 : start.sh passe alors par le volume.

Variables :
  LOCAL_MODELS_DIR           dossier local (defaut /models-local)
  HUGGINGFACE_ACCESS_TOKEN   cle Hugging Face
  FETCH_PARALLEL             fichiers telecharges en meme temps (defaut 3)
  YAML_PATH                  defaut /comfyui/extra_model_paths.yaml
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

os.environ.setdefault("HF_XET_HIGH_PERFORMANCE", "1")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")

from download_models import hf_token, load_manifest, target_path, wanted_files  # noqa: E402
from stage_models import yaml_for  # noqa: E402


def log(message: str) -> None:
    print(f"[fetch_models] {message}", flush=True)


def fetch_one(base: Path, entry: dict, token: str | None) -> str:
    from huggingface_hub import hf_hub_download

    final_path = target_path(base, entry)
    if final_path.is_file():
        return f"deja present : {final_path.name}"
    tmp_dir = base / ".tmp" / entry["target_dir"]
    tmp_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    downloaded = hf_hub_download(
        repo_id=entry["repo_id"],
        filename=entry["path_in_repo"],
        local_dir=str(tmp_dir),
        token=token,
    )
    final_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(downloaded, final_path)
    seconds = time.time() - started
    size = final_path.stat().st_size
    return (f"OK : {final_path.name} ({size / 1e9:.1f} Go en {seconds:.0f} s, "
            f"{size / 1e6 / max(seconds, 0.1):.0f} Mo/s)")


def main() -> int:
    base = Path(os.environ.get("LOCAL_MODELS_DIR", "/models-local"))
    yaml_path = Path(os.environ.get("YAML_PATH", "/comfyui/extra_model_paths.yaml"))
    parallel = max(1, int(os.environ.get("FETCH_PARALLEL", "3")))
    entries = wanted_files(load_manifest(), include_optional=False)
    token = hf_token()
    if token is None:
        log("Aucune cle Hugging Face : passage par le volume.")
        return 1

    base.mkdir(parents=True, exist_ok=True)
    log(f"Telechargement de {len(entries)} fichiers vers {base} ({parallel} en parallele) ...")
    started = time.time()
    # Les plus gros d'abord : ils fixent la duree totale.
    entries = sorted(entries, key=lambda e: "diffusion" not in e["target_dir"])
    try:
        with ThreadPoolExecutor(max_workers=parallel) as pool:
            futures = [pool.submit(fetch_one, base, e, token) for e in entries]
            for future in as_completed(futures):
                log(future.result())
    except Exception as error:  # noqa: BLE001 - message lisible dans les logs
        log(f"ECHEC ({error}) : passage par le volume.")
        return 1
    finally:
        shutil.rmtree(base / ".tmp", ignore_errors=True)

    log(f"Termine en {time.time() - started:.0f} s.")
    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(yaml_for(base), encoding="utf-8")
    log(f"ComfyUI lira les modeles depuis {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
