#!/usr/bin/env bash
# Point d'entree du worker : modeles -> ComfyUI -> handler RunPod.
set -euo pipefail

MODELS_DIR="${MODELS_DIR:-/runpod-volume/models}"
export MODELS_DIR

echo "[start] Verification des modeles dans ${MODELS_DIR}"
# Un echec de telechargement ne doit pas faire redemarrer le worker en boucle
# (ce qui couterait du GPU pour rien) : on continue, et la requete "health"
# indiquera les modeles manquants.
python /app/download_models.py || echo "[start] ATTENTION : modeles incomplets, voir les logs ci-dessus."

# Copie des modeles sur le disque local (bien plus rapide a lire que le volume).
python /app/stage_models.py || echo "[start] ATTENTION : copie locale impossible, lecture depuis le volume."

echo "[start] Demarrage de ComfyUI"
python /comfyui/main.py \
  --listen 127.0.0.1 \
  --port 8188 \
  --disable-auto-launch \
  --extra-model-paths-config /comfyui/extra_model_paths.yaml \
  ${COMFY_EXTRA_ARGS:-} &

echo "[start] Demarrage du handler RunPod"
exec python -u /app/handler.py
