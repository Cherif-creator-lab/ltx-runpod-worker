#!/usr/bin/env bash
# Point d'entree du worker : modeles -> ComfyUI -> handler RunPod.
set -euo pipefail

MODELS_DIR="${MODELS_DIR:-/runpod-volume/models}"
export MODELS_DIR

# 1) Voie rapide : telechargement direct depuis Hugging Face vers le disque local.
# 2) Secours : modeles du volume RunPod (telechargement si absents), puis copie locale.
if [ "${MODEL_SOURCE:-huggingface}" = "huggingface" ] && python /app/fetch_models_local.py; then
  echo "[start] Modeles prets sur le disque local (Hugging Face)."
else
  echo "[start] Verification des modeles dans ${MODELS_DIR}"
  # Un echec ne doit pas faire redemarrer le worker en boucle (GPU facture pour rien).
  python /app/download_models.py || echo "[start] ATTENTION : modeles incomplets, voir les logs ci-dessus."
  python /app/stage_models.py || echo "[start] ATTENTION : copie locale impossible, lecture depuis le volume."
fi

echo "[start] Demarrage de ComfyUI"
python /comfyui/main.py \
  --listen 127.0.0.1 \
  --port 8188 \
  --disable-auto-launch \
  --extra-model-paths-config /comfyui/extra_model_paths.yaml \
  ${COMFY_EXTRA_ARGS:-} &

echo "[start] Demarrage du handler RunPod"
exec python -u /app/handler.py
