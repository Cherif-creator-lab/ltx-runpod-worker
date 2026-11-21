"""Remplit un workflow ComfyUI (format API) avec les valeurs d'une requete.

Les noeuds a modifier sont reperes par leur titre (_meta.title). Ces titres sont
deja poses dans les fichiers de workflows/ (voir workflows/README.md) :

  INPUT_PROMPT      prompt positif               (champ value / text / prompt)
  INPUT_NEGATIVE    prompt negatif (optionnel)   (champ text / value)
  INPUT_IMAGE       image de depart              (LoadImage)
  INPUT_END_IMAGE   image de fin (workflow FLF2V)
  INPUT_WIDTH       largeur finale               (champ value / width)
  INPUT_HEIGHT      hauteur finale               (champ value / height)
  INPUT_SECONDS     duree en secondes (entier)   (champ value)
  INPUT_FPS         images par seconde           (champ value / fps / frame_rate)
  INPUT_SEED        seed                         (champ noise_seed / seed)
  OUTPUT_VIDEO      noeud SaveVideo

Pas de detection automatique : elle se trompait (elle pouvait remplir le prompt
negatif). Un titre obligatoire absent produit une erreur claire.
Ce module n'a aucune dependance et est teste dans tests/.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

FIELDS = {
    "INPUT_PROMPT": ("value", "text", "prompt", "string"),
    "INPUT_NEGATIVE": ("text", "value", "prompt", "string"),
    "INPUT_IMAGE": ("image",),
    "INPUT_END_IMAGE": ("image",),
    "INPUT_WIDTH": ("value", "width"),
    "INPUT_HEIGHT": ("value", "height"),
    "INPUT_SECONDS": ("value", "seconds", "duration"),
    "INPUT_FPS": ("value", "fps", "frame_rate"),
    "INPUT_SEED": ("noise_seed", "seed", "value"),
}
REQUIRED = ("INPUT_PROMPT", "INPUT_IMAGE", "INPUT_WIDTH", "INPUT_HEIGHT", "INPUT_SECONDS")

# Le workflow LTX-2.5 genere d'abord en demi-resolution puis agrandit x2 :
# la taille finale doit donc etre un multiple de 64.
SIZE_MULTIPLE = 64


class WorkflowError(ValueError):
    """Erreur lisible renvoyee au logiciel appelant."""


@dataclass
class JobParams:
    prompt: str
    image_name: str
    end_image_name: str | None = None
    negative_prompt: str | None = None
    width: int = 704
    height: int = 1280
    seconds: int = 6
    fps: int = 24
    seed: int = 0


@dataclass
class PatchReport:
    touched: dict[str, list[str]] = field(default_factory=dict)

    def add(self, what: str, node_id: str) -> None:
        self.touched.setdefault(what, []).append(node_id)


def round_to_multiple(value: int, multiple: int = SIZE_MULTIPLE, minimum: int = 256) -> int:
    return max(minimum, int(round(value / multiple)) * multiple)


def frame_count(seconds: int, fps: int) -> int:
    """Nombre d'images produit par le workflow (secondes x fps + 1)."""
    return seconds * fps + 1


def nodes_titled(workflow: dict, title: str) -> list[str]:
    wanted = title.upper()
    return [nid for nid, node in workflow.items()
            if str(node.get("_meta", {}).get("title", "")).strip().upper() == wanted]


def _set_value(workflow: dict, node_id: str, title: str, value) -> None:
    inputs = workflow[node_id].setdefault("inputs", {})
    for key in FIELDS[title]:
        if key in inputs:
            # Remplace aussi une valeur liee a un autre noeud : la requete fixe la valeur.
            inputs[key] = value
            return
    raise WorkflowError(f"Le noeud {node_id} ({title}) n'a aucun des champs {FIELDS[title]}.")


def patch_workflow(workflow: dict, params: JobParams) -> tuple[dict, PatchReport]:
    """Renvoie une copie du workflow remplie avec les valeurs de la requete."""
    if not isinstance(workflow, dict) or not workflow:
        raise WorkflowError("Workflow vide ou invalide (format API attendu).")
    if "nodes" in workflow and "links" in workflow:
        raise WorkflowError("Workflow au format interface : exporter avec Export (API).")

    missing = [t for t in REQUIRED if not nodes_titled(workflow, t)]
    if params.end_image_name and not nodes_titled(workflow, "INPUT_END_IMAGE"):
        missing.append("INPUT_END_IMAGE")
    if missing:
        raise WorkflowError(f"Titres absents du workflow : {', '.join(missing)}.")

    values = {
        "INPUT_PROMPT": params.prompt,
        "INPUT_IMAGE": params.image_name,
        "INPUT_WIDTH": params.width,
        "INPUT_HEIGHT": params.height,
        "INPUT_SECONDS": params.seconds,
        "INPUT_FPS": params.fps,
        "INPUT_SEED": params.seed,
    }
    if params.negative_prompt is not None:
        values["INPUT_NEGATIVE"] = params.negative_prompt
    if params.end_image_name:
        values["INPUT_END_IMAGE"] = params.end_image_name

    wf = copy.deepcopy(workflow)
    report = PatchReport()
    for title, value in values.items():
        for node_id in nodes_titled(wf, title):
            _set_value(wf, node_id, title, value)
            report.add(title, node_id)
    return wf, report


VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".mkv")


def find_video_outputs(history_entry: dict, preferred_node_ids: list[str] | None = None):
    """Liste les fichiers video produits, ceux du noeud OUTPUT_VIDEO en premier."""
    preferred = list(preferred_node_ids or [])
    outputs = history_entry.get("outputs", {}) or {}
    order = preferred + [n for n in outputs if n not in preferred]
    found = []
    for node_id in order:
        for value in (outputs.get(node_id) or {}).values():
            if not isinstance(value, list):
                continue
            for item in value:
                if isinstance(item, dict) and str(item.get("filename", "")).lower().endswith(VIDEO_EXTENSIONS):
                    found.append(item)
    return found
