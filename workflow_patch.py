"""Remplit un workflow ComfyUI (format API) avec les valeurs d'une requete.

Les noeuds a modifier sont trouves en priorite par leur titre (_meta.title),
que l'on fixe dans ComfyUI avant l'export :
  INPUT_PROMPT      texte positif
  INPUT_NEGATIVE    texte negatif (optionnel)
  INPUT_IMAGE       image de depart (Load Image)
  INPUT_END_IMAGE   image de fin (Load Image, workflow FLF2V)
  INPUT_SIZE        noeud qui porte width / height / length
  INPUT_SEED        noeud qui porte le seed
  OUTPUT_VIDEO      noeud de sauvegarde de la video

Si un titre manque, une detection automatique prend le relais
(par exemple : tous les noeuds qui ont width + height + length).
Ce module n'a aucune dependance : il est teste dans tests/.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

TEXT_KEYS = ("text", "prompt", "value", "string")
SEED_KEYS = ("noise_seed", "seed")
FPS_KEYS = ("frame_rate", "fps")


class WorkflowError(ValueError):
    """Erreur lisible renvoyee au logiciel appelant."""


@dataclass
class JobParams:
    prompt: str
    image_name: str
    end_image_name: str | None = None
    negative_prompt: str | None = None
    width: int = 720
    height: int = 1280
    length: int = 145
    fps: int = 24
    seed: int = 0


@dataclass
class PatchReport:
    touched: dict[str, list[str]] = field(default_factory=dict)

    def add(self, what: str, node_id: str) -> None:
        self.touched.setdefault(what, []).append(node_id)


def round_to_multiple(value: int, multiple: int, minimum: int) -> int:
    return max(minimum, int(round(value / multiple)) * multiple)


def frames_for_seconds(seconds: float, fps: int) -> int:
    """LTX attend un nombre d'images de la forme 8n + 1."""
    blocks = max(1, int(round(seconds * fps / 8)))
    return blocks * 8 + 1


def _title(node: dict) -> str:
    return str(node.get("_meta", {}).get("title", "")).strip()


def _nodes_titled(workflow: dict, title: str) -> list[str]:
    wanted = title.upper()
    return [nid for nid, node in workflow.items() if _title(node).upper() == wanted]


def _set_first_key(node: dict, keys: tuple[str, ...], value) -> str | None:
    inputs = node.setdefault("inputs", {})
    for key in keys:
        if key in inputs and not isinstance(inputs[key], list):
            inputs[key] = value
            return key
    return None


def _sorted_ids(ids):
    def key(nid: str):
        parts = str(nid).replace(":", ".").split(".")
        return [int(p) if p.isdigit() else p for p in parts]

    return sorted(ids, key=key)


def _is_text_encoder(node: dict) -> bool:
    inputs = node.get("inputs", {})
    has_text = any(k in inputs and isinstance(inputs[k], str) for k in TEXT_KEYS)
    return has_text and "encode" in node.get("class_type", "").lower()


def _patch_prompt(wf: dict, params: JobParams, report: PatchReport) -> None:
    ids = _nodes_titled(wf, "INPUT_PROMPT")
    if not ids:
        candidates = [
            nid for nid, node in wf.items()
            if _is_text_encoder(node) and "neg" not in _title(node).lower()
        ]
        positives = [nid for nid in candidates if "pos" in _title(wf[nid]).lower()]
        if len(positives) == 1:
            ids = positives
        elif len(candidates) == 1:
            ids = candidates
        else:
            raise WorkflowError(
                "Impossible de trouver le noeud du prompt. Dans ComfyUI, renommer le "
                "noeud du texte positif en INPUT_PROMPT, puis re-exporter (API)."
            )
    for nid in ids:
        if _set_first_key(wf[nid], TEXT_KEYS, params.prompt) is None:
            raise WorkflowError(f"Le noeud {nid} (INPUT_PROMPT) n'a pas de champ texte.")
        report.add("prompt", nid)

    if params.negative_prompt is not None:
        for nid in _nodes_titled(wf, "INPUT_NEGATIVE"):
            if _set_first_key(wf[nid], TEXT_KEYS, params.negative_prompt):
                report.add("negative_prompt", nid)


def _load_image_ids(wf: dict) -> list[str]:
    return _sorted_ids(nid for nid, n in wf.items() if n.get("class_type") == "LoadImage")


def _patch_images(wf: dict, params: JobParams, report: PatchReport) -> None:
    start_ids = _nodes_titled(wf, "INPUT_IMAGE")
    end_ids = _nodes_titled(wf, "INPUT_END_IMAGE")
    loaders = _load_image_ids(wf)

    if not start_ids:
        remaining = [nid for nid in loaders if nid not in end_ids]
        if not remaining:
            raise WorkflowError("Le workflow ne contient aucun noeud Load Image.")
        start_ids = remaining[:1]

    if params.end_image_name and not end_ids:
        remaining = [nid for nid in loaders if nid not in start_ids]
        if not remaining:
            raise WorkflowError(
                "Une image de fin a ete envoyee mais le workflow n'a pas de second "
                "Load Image. Utiliser le workflow FLF2V (titre INPUT_END_IMAGE)."
            )
        end_ids = remaining[:1]

    for nid in start_ids:
        wf[nid].setdefault("inputs", {})["image"] = params.image_name
        report.add("image", nid)
    if params.end_image_name:
        for nid in end_ids:
            wf[nid].setdefault("inputs", {})["image"] = params.end_image_name
            report.add("end_image", nid)


def _patch_size(wf: dict, params: JobParams, report: PatchReport) -> None:
    ids = _nodes_titled(wf, "INPUT_SIZE")
    if not ids:
        ids = [
            nid for nid, node in wf.items()
            if {"width", "height"} <= set(node.get("inputs", {}))
            and node.get("class_type") != "LoadImage"
        ]
    if not ids:
        raise WorkflowError(
            "Impossible de trouver le noeud de taille. Renommer en INPUT_SIZE le noeud "
            "qui porte width / height / length, puis re-exporter (API)."
        )
    for nid in ids:
        inputs = wf[nid].setdefault("inputs", {})
        for key, value in (("width", params.width), ("height", params.height)):
            if key in inputs and not isinstance(inputs[key], list):
                inputs[key] = value
        report.add("size", nid)

    length_done = False
    for nid, node in wf.items():
        inputs = node.get("inputs", {})
        if "length" in inputs and not isinstance(inputs["length"], list):
            inputs["length"] = params.length
            report.add("length", nid)
            length_done = True
        for key in FPS_KEYS:
            if key in inputs and not isinstance(inputs[key], list):
                inputs[key] = params.fps
                report.add("fps", nid)
                break
    if not length_done:
        raise WorkflowError("Aucun noeud avec un champ 'length' : duree non reglable.")


def _patch_seed(wf: dict, params: JobParams, report: PatchReport) -> None:
    ids = _nodes_titled(wf, "INPUT_SEED") or [
        nid for nid, node in wf.items()
        if any(k in node.get("inputs", {}) for k in SEED_KEYS)
    ]
    for nid in ids:
        if _set_first_key(wf[nid], SEED_KEYS, params.seed):
            report.add("seed", nid)


def patch_workflow(workflow: dict, params: JobParams) -> tuple[dict, PatchReport]:
    """Renvoie une copie du workflow remplie avec les valeurs de la requete."""
    if not isinstance(workflow, dict) or not workflow:
        raise WorkflowError("Workflow vide ou invalide (format API attendu).")
    if "nodes" in workflow and "links" in workflow:
        raise WorkflowError(
            "Ce workflow est au format interface, pas au format API. Dans ComfyUI : "
            "Workflow > Export (API)."
        )
    wf = copy.deepcopy(workflow)
    report = PatchReport()
    _patch_prompt(wf, params, report)
    _patch_images(wf, params, report)
    _patch_size(wf, params, report)
    _patch_seed(wf, params, report)
    return wf, report


VIDEO_EXTENSIONS = (".mp4", ".webm", ".mov", ".mkv")


def find_video_outputs(history_entry: dict, preferred_node_ids: list[str] | None = None):
    """Liste les fichiers video produits, ceux du noeud OUTPUT_VIDEO en premier."""
    outputs = history_entry.get("outputs", {}) or {}
    order = list(preferred_node_ids or []) + [n for n in outputs if n not in (preferred_node_ids or [])]
    found = []
    for node_id in order:
        node_output = outputs.get(node_id) or {}
        for value in node_output.values():
            if not isinstance(value, list):
                continue
            for item in value:
                if isinstance(item, dict) and str(item.get("filename", "")).lower().endswith(VIDEO_EXTENSIONS):
                    found.append(item)
    return found
