"""Copie les modeles du volume RunPod vers le disque local du worker.

Pourquoi : le Global volume se lit lentement (~30 Mo/s en lecture aleatoire) et
ComfyUI charge les poids a la demande. Lire 45 Go ainsi depasse 15 minutes.
Une copie sequentielle vers le disque local, puis une lecture locale, est bien
plus rapide.

Ecrit ensuite /comfyui/extra_model_paths.yaml vers le dossier choisi :
  - le disque local si la copie a reussi,
  - sinon le volume (comportement d'avant, plus lent mais fonctionnel).

Variables :
  MODELS_DIR         modeles sur le volume (defaut /runpod-volume/models)
  LOCAL_MODELS_DIR   copie locale (defaut /models-local) ; "off" pour desactiver
  YAML_PATH          fichier a ecrire (defaut /comfyui/extra_model_paths.yaml)
"""

from __future__ import annotations

import os
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SUBDIRS = ("diffusion_models", "text_encoders", "vae", "latent_upscale_models",
           "loras", "checkpoints")
CHUNK = 64 * 1024 * 1024  # lectures de 64 Mo : sequentielles, rapides sur le volume
SPACE_MARGIN = 3 * 1024**3


def log(message: str) -> None:
    print(f"[stage_models] {message}", flush=True)


def yaml_for(base: Path) -> str:
    lines = ["runpod_models:", f"  base_path: {base}"]
    lines += [f"  {name}: {name}" for name in SUBDIRS]
    return "\n".join(lines) + "\n"


def model_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(p for p in root.rglob("*.safetensors")
                  if ".download_tmp" not in p.parts and p.is_file())


# Mesure du 21/11 : 16 lectures paralleles = 41 Mo/s, 1 lecture = 66 Mo/s.
THREADS = int(os.environ.get("STAGE_THREADS", "1"))


def copy_file(src: Path, dst: Path) -> float:
    """Copie par morceaux lus en parallele : le volume reseau est limite par flux,
    plusieurs lectures simultanees multiplient le debit."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(dst.suffix + ".part")
    size = src.stat().st_size
    started = time.time()
    src_fd = os.open(src, os.O_RDONLY)
    dst_fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    try:
        os.ftruncate(dst_fd, size)

        def copy_range(offset: int) -> None:
            end = min(offset + CHUNK, size)
            position = offset
            while position < end:
                data = os.pread(src_fd, end - position, position)
                if not data:
                    raise OSError(f"Lecture interrompue dans {src} a {position}")
                written = os.pwrite(dst_fd, data, position)
                position += written

        with ThreadPoolExecutor(max_workers=THREADS) as pool:
            list(pool.map(copy_range, range(0, size, CHUNK)))
    finally:
        os.close(src_fd)
        os.close(dst_fd)
    if tmp.stat().st_size != size:
        raise OSError(f"Taille incorrecte apres copie de {src}")
    tmp.replace(dst)
    return time.time() - started


def stage(source: Path, target: Path) -> bool:
    files = model_files(source)
    if not files:
        log(f"Aucun modele trouve dans {source}.")
        return False

    todo = []
    for src in files:
        dst = target / src.relative_to(source)
        if not (dst.is_file() and dst.stat().st_size == src.stat().st_size):
            todo.append((src, dst))
    if not todo:
        log(f"Modeles deja presents en local ({len(files)} fichiers).")
        return True

    needed = sum(src.stat().st_size for src, _ in todo)
    target.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(target).free
    if free < needed + SPACE_MARGIN:
        log(f"Disque local trop petit : {needed / 1e9:.1f} Go a copier, {free / 1e9:.1f} Go libres. "
            "Augmenter 'Container disk' de l'endpoint (80 Go conseilles).")
        return False

    log(f"Copie de {len(todo)} fichiers ({needed / 1e9:.1f} Go) vers {target} ...")
    started = time.time()
    for src, dst in todo:
        seconds = copy_file(src, dst)
        size = src.stat().st_size
        log(f"OK : {dst.name} ({size / 1e9:.1f} Go en {seconds:.0f} s, "
            f"{size / 1e6 / max(seconds, 0.1):.0f} Mo/s)")
    log(f"Copie terminee en {time.time() - started:.0f} s.")
    return True


def main() -> int:
    source = Path(os.environ.get("MODELS_DIR", "/runpod-volume/models"))
    local = os.environ.get("LOCAL_MODELS_DIR", "/models-local")
    yaml_path = Path(os.environ.get("YAML_PATH", "/comfyui/extra_model_paths.yaml"))

    base = source
    if local.lower() not in ("", "off", "false", "0"):
        try:
            if stage(source, Path(local)):
                base = Path(local)
        except OSError as error:
            log(f"Copie impossible ({error}) : lecture directe depuis le volume.")

    yaml_path.parent.mkdir(parents=True, exist_ok=True)
    yaml_path.write_text(yaml_for(base), encoding="utf-8")
    log(f"ComfyUI lira les modeles depuis {base}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
