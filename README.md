# ltx-runpod-worker

Endpoint RunPod Serverless qui transforme **une image + un prompt** en **clip MP4 LTX-2.5 avec son**.
Utilise par le Studio Video (chaine LE CORPS HUMAIN SANS FILTRE).

- CUDA 12.8 + PyTorch officiel (compatible avec la plupart des GPU RunPod)
- ComfyUI officiel + workflows officiels LTX-2.5 (I2V et FLF2V)
- Modeles sur le Global volume RunPod (`/runpod-volume/models`), telecharges une seule fois

## Contenu

| Fichier | Role |
| --- | --- |
| `Dockerfile` | Image construite par RunPod depuis ce depot |
| `start.sh` | Demarrage : modeles -> ComfyUI -> handler |
| `download_models.py` + `models_manifest.json` | Telechargement des modeles manquants depuis Hugging Face |
| `extra_model_paths.yaml` | Fait lire a ComfyUI les modeles du volume |
| `handler.py` | Recoit la requete, remplit le workflow, renvoie la video |
| `workflow_patch.py` | Remplissage du workflow (teste dans `tests/`) |
| `workflows/` | **A completer** : les 2 workflows exportes en format API (voir `workflows/README.md`) |
| `test_input.json` | Requete de test a coller dans l'onglet Requests de RunPod |
| `scripts/call_endpoint.py` | Exemple d'appel depuis le Studio Video ou ton PC |

## Mise en route

1. **Hugging Face** : accepter les conditions de `Lightricks/LTX-2.5`. Creer une cle *Read*.
2. **RunPod > Secrets** : secret `hf_token` = la cle Hugging Face.
3. **Workflows** : exporter `ltx25_i2v_api.json` et `ltx25_flf2v_api.json` dans `workflows/`, puis pousser.
4. **RunPod > Serverless > New Endpoint > Deploy from a GitHub repository** : ce depot, branche `main`.
5. Reglages de l'endpoint :

| Reglage | Valeur |
| --- | --- |
| Container disk | 30 GB |
| Variable `HUGGINGFACE_ACCESS_TOKEN` | `{{ RUNPOD_SECRET_hf_token }}` |
| Type | Queue |
| GPU | 48 GB |
| Max workers / Active workers | 1 / 0 |
| Idle timeout | 60 s |
| Initialization timeout | 3600 s (premier telechargement des modeles) |
| Execution timeout | 900 s |
| FlashBoot | active |
| Data centers | tous |
| Global volume | `ltx-global` |
| Allowed CUDA versions | 12.8 et plus (pas "13.0 seulement") |

6. **Test** : onglet Requests > coller `test_input.json` > Run. Le premier appel telecharge les modeles
   (20 a 40 min, une seule fois). Puis `{"input": {"action": "health"}}` doit lister les modeles.

## API

Requete :

```json
{"input": {
  "image": "<base64>", "end_image": "<base64, optionnel>",
  "prompt": "...", "negative_prompt": "optionnel",
  "seconds": 6, "width": 720, "height": 1280, "fps": 24, "seed": 42
}}
```

`image_url` / `end_image_url` sont acceptes a la place du base64.

Reponse :

```json
{"video_base64": "...", "seconds": 6.0, "frames": 145, "width": 736, "height": 1280,
 "fps": 24, "seed": 42, "generation_time_s": 95.3, "size_mb": 4.2}
```

Ou `video_url` au lieu de `video_base64` si un stockage S3/R2 est configure
(`BUCKET_ENDPOINT_URL`, `BUCKET_ACCESS_KEY_ID`, `BUCKET_SECRET_ACCESS_KEY`, `BUCKET_NAME`,
optionnel `BUCKET_PUBLIC_URL`). Conseille pour le 1080p : au-dela de 15 Mo, le base64 est refuse.

En cas d'erreur : `{"error": "message lisible"}`.

## Appel depuis le Studio Video

`scripts/call_endpoint.py` montre le cycle complet : `POST /run`, suivi `GET /status/<id>`
toutes les 5 s, enregistrement du MP4. `RUNPOD_API_KEY` et `RUNPOD_ENDPOINT_ID` se lisent dans `.env`,
uniquement cote serveur.

## Tests

```bash
python -m unittest discover -s tests
```

## Points a surveiller

- `COMFYUI_REF` dans le Dockerfile vaut `master` : une fois que tout marche, le remplacer par le
  commit utilise pour figer la version.
- Le premier telechargement des modeles est facture en temps GPU (~1 $).
- Frais IOPS du Global volume : a verifier sur la facture le premier mois.
