# Worker RunPod Serverless pour LTX-2.5 (image -> video) avec ComfyUI.
# CUDA 12.8 : compatible avec la grande majorite des GPU RunPod (pas de CUDA 13,
# qui ne tourne que sur tres peu de machines).
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    VIRTUAL_ENV=/opt/venv \
    PATH=/opt/venv/bin:$PATH

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-dev build-essential \
        git ffmpeg wget ca-certificates libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv /opt/venv

# PyTorch recent compile pour CUDA 12.8 (les noeuds LTX-2.5 de ComfyUI demandent un PyTorch recent).
RUN pip install --upgrade pip \
    && pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu128

# Version de ComfyUI : "master" par defaut. Une fois que tout fonctionne, remplacer par
# le commit utilise pour figer la version (ex. --build-arg COMFYUI_REF=<commit>).
ARG COMFYUI_REF=master
RUN git clone https://github.com/comfyanonymous/ComfyUI.git /comfyui \
    && cd /comfyui \
    && git checkout ${COMFYUI_REF} \
    && pip install -r requirements.txt

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install -r /app/requirements.txt

COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml
COPY models_manifest.json download_models.py stage_models.py handler.py workflow_patch.py start.sh /app/
COPY workflows /app/workflows

RUN chmod +x /app/start.sh

CMD ["/app/start.sh"]
