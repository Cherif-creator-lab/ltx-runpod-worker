"""Telecharge les modeles LTX-2.5 sur le volume RunPod s'ils manquent.

Lance une fois au demarrage du worker (start.sh). Les fichiers deja presents
ne sont pas retelecharges. Un fichier n'apparait dans son dossier final
qu'une fois completement telecharge (ecriture dans un dossier temporaire,
puis deplacement).

Variables d'environnement :
  MODELS_DIR                 dossier des modeles (defaut : /runpod-volume/models)
  HUGGINGFACE_ACCESS_TOKEN   cle Hugging Face (aussi lue : HF_TOKEN)
  DOWNLOAD_OPTIONAL_MODELS   "true" pour telecharger aussi les fichiers optionnels
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
from pathlib import Path

MANIFEST_PATH = Path(__file__).with_name("models_manifest.json")
LOCK_STALE_SECONDS = 3 * 60 * 60  # un verrou plus vieux que 3 h est considere abandonne


def log(message: str) -> None:
    print(f"[download_models] {message}", flush=True)


def models_dir() -> Path:
    explicit = os.environ.get("MODELS_DIR")
    if explicit:
        return Path(explicit)
    return Path("/runpod-volume/models")


def hf_token() -> str | None:
    for name in ("HUGGINGFACE_ACCESS_TOKEN", "HF_TOKEN", "HUGGINGFACE_TOKEN"):
        value = os.environ.get(name, "").strip()
        if value and not value.startswith("{{"):
            return value
    return None


def load_manifest() -> list[dict]:
    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        return json.load(handle)["files"]


def wanted_files(entries: list[dict], include_optional: bool) -> list[dict]:
    return [e for e in entries if include_optional or not e.get("optional")]


def target_path(base: Path, entry: dict) -> Path:
    return base / entry["target_dir"] / Path(entry["path_in_repo"]).name


def missing_files(base: Path, entries: list[dict]) -> list[dict]:
    return [e for e in entries if not target_path(base, e).is_file()]


def acquire_lock(lock_path: Path) -> bool:
    """Evite que deux workers telechargent en meme temps sur le meme volume."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return True
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except FileNotFoundError:
                continue
            if age > LOCK_STALE_SECONDS:
                log("Verrou abandonne detecte, suppression.")
                lock_path.unlink(missing_ok=True)
                continue
            log("Un autre worker telecharge deja les modeles, attente 30 s...")
            time.sleep(30)
            return False


def download_one(base: Path, entry: dict, token: str | None) -> None:
    from huggingface_hub import hf_hub_download

    final_path = target_path(base, entry)
    tmp_dir = base / ".download_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    log(f"Telechargement de {entry['repo_id']}/{entry['path_in_repo']} ...")
    started = time.time()
    downloaded = hf_hub_download(
        repo_id=entry["repo_id"],
        filename=entry["path_in_repo"],
        local_dir=str(tmp_dir),
        token=token,
    )
    final_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(downloaded, final_path)
    size_gb = final_path.stat().st_size / 1e9
    log(f"OK : {final_path} ({size_gb:.1f} Go en {time.time() - started:.0f} s)")


def main() -> int:
    base = models_dir()
    include_optional = os.environ.get("DOWNLOAD_OPTIONAL_MODELS", "").lower() == "true"
    entries = wanted_files(load_manifest(), include_optional)

    if not Path("/runpod-volume").exists() and "MODELS_DIR" not in os.environ:
        log("ATTENTION : aucun volume monte sur /runpod-volume. Les modeles seront "
            "retelecharges a chaque demarrage. Attacher le Global volume a l'endpoint.")

    todo = missing_files(base, entries)
    if not todo:
        log("Tous les modeles sont deja presents.")
        return 0

    token = hf_token()
    if token is None:
        log("ATTENTION : aucune cle Hugging Face trouvee (HUGGINGFACE_ACCESS_TOKEN). "
            "Le depot Lightricks/LTX-2.5 est protege, le telechargement va echouer.")

    lock_path = base / ".download.lock"
    while not acquire_lock(lock_path):
        todo = missing_files(base, entries)
        if not todo:
            log("Modeles telecharges par un autre worker.")
            return 0

    try:
        for entry in missing_files(base, entries):
            download_one(base, entry, token)
    except Exception as error:  # noqa: BLE001 - on veut un message lisible dans les logs
        log(f"ERREUR pendant le telechargement : {error}")
        return 1
    finally:
        lock_path.unlink(missing_ok=True)
        shutil.rmtree(base / ".download_tmp", ignore_errors=True)

    log("Tous les modeles sont prets.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
