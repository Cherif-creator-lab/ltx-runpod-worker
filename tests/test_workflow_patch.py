"""Tests du remplissage de workflow. Lancer : python -m unittest discover -s tests"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from workflow_patch import (  # noqa: E402
    JobParams,
    WorkflowError,
    find_video_outputs,
    frame_count,
    nodes_titled,
    patch_workflow,
    round_to_multiple,
)

I2V = ROOT / "workflows" / "ltx25_i2v_api.json"


def params(**overrides):
    base = dict(prompt="a mite walks", image_name="start.png", width=704, height=1280,
                seconds=6, fps=24, seed=42)
    base.update(overrides)
    return JobParams(**base)


class TestHelpers(unittest.TestCase):
    def test_frames_are_8n_plus_1(self):
        for seconds in range(1, 21):
            self.assertEqual((frame_count(seconds, 24) - 1) % 8, 0)
        self.assertEqual(frame_count(6, 24), 145)

    def test_round_to_multiple_of_64(self):
        self.assertEqual(round_to_multiple(720), 704)
        self.assertEqual(round_to_multiple(1080), 1088)
        self.assertEqual(round_to_multiple(1920), 1920)
        self.assertEqual(round_to_multiple(10), 256)


class TestOfficialI2VWorkflow(unittest.TestCase):
    """Teste le vrai workflow exporte depuis ComfyUI."""

    def setUp(self):
        self.wf = json.loads(I2V.read_text(encoding="utf-8"))

    def test_titles_present_once(self):
        for title in ("INPUT_PROMPT", "INPUT_NEGATIVE", "INPUT_IMAGE", "INPUT_WIDTH",
                      "INPUT_HEIGHT", "INPUT_SECONDS", "INPUT_FPS", "INPUT_SEED", "OUTPUT_VIDEO"):
            self.assertEqual(len(nodes_titled(self.wf, title)), 1, title)

    def test_patch_sets_the_right_nodes(self):
        out, report = patch_workflow(self.wf, params(negative_prompt="blurry"))
        self.assertEqual(out["398:376"]["inputs"]["value"], "a mite walks")   # prompt positif
        self.assertEqual(out["398:373"]["inputs"]["text"], "blurry")          # prompt negatif
        self.assertEqual(out["395"]["inputs"]["image"], "start.png")
        self.assertEqual(out["398:372"]["inputs"]["value"], 704)
        self.assertEqual(out["398:360"]["inputs"]["value"], 1280)
        self.assertEqual(out["398:362"]["inputs"]["value"], 6)
        self.assertEqual(out["398:361"]["inputs"]["value"], 24)
        self.assertEqual(out["398:339"]["inputs"]["noise_seed"], 42)
        # le prompt positif ne doit jamais atterrir dans le negatif
        self.assertNotEqual(out["398:373"]["inputs"]["text"], "a mite walks")
        # les liens du graphe restent intacts
        self.assertEqual(out["398:364"]["inputs"]["text"], ["398:382", 0])
        self.assertEqual(out["398:356"]["inputs"]["length"], ["398:378", 1])

    def test_negative_kept_when_not_given(self):
        out, _ = patch_workflow(self.wf, params())
        self.assertEqual(out["398:373"]["inputs"]["text"], self.wf["398:373"]["inputs"]["text"])

    def test_every_link_points_to_existing_node(self):
        out, _ = patch_workflow(self.wf, params())
        for node in out.values():
            for value in node["inputs"].values():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                    self.assertIn(value[0], out)

    def test_end_image_needs_flf2v(self):
        with self.assertRaises(WorkflowError):
            patch_workflow(self.wf, params(end_image_name="end.png"))

    def test_original_not_modified(self):
        before = json.dumps(self.wf, sort_keys=True)
        patch_workflow(self.wf, params())
        self.assertEqual(before, json.dumps(self.wf, sort_keys=True))


class TestErrors(unittest.TestCase):
    def test_missing_titles(self):
        wf = {"1": {"class_type": "LoadImage", "inputs": {"image": "x"}, "_meta": {"title": "Load"}}}
        with self.assertRaises(WorkflowError) as ctx:
            patch_workflow(wf, params())
        self.assertIn("INPUT_PROMPT", str(ctx.exception))

    def test_ui_format_rejected(self):
        with self.assertRaises(WorkflowError):
            patch_workflow({"nodes": [], "links": []}, params())


class TestFindVideo(unittest.TestCase):
    def test_prefers_output_node(self):
        entry = {"outputs": {
            "3": {"images": [{"filename": "preview.png"}]},
            "9": {"images": [{"filename": "other.mp4"}]},
            "75": {"images": [{"filename": "final.mp4", "subfolder": "video", "type": "output"}],
                   "animated": [True]},
        }}
        found = find_video_outputs(entry, ["75"])
        self.assertEqual(found[0]["filename"], "final.mp4")
        self.assertEqual(len(found), 2)

    def test_none(self):
        self.assertEqual(find_video_outputs({"outputs": {}}), [])


if __name__ == "__main__":
    unittest.main()
