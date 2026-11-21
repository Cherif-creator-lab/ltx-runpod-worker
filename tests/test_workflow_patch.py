"""Tests du remplissage de workflow. Lancer : python -m unittest discover -s tests"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_patch import (  # noqa: E402
    JobParams,
    WorkflowError,
    find_video_outputs,
    frames_for_seconds,
    patch_workflow,
    round_to_multiple,
)


def titled_workflow():
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": "x.png"}, "_meta": {"title": "INPUT_IMAGE"}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": "old", "clip": ["9", 0]},
              "_meta": {"title": "INPUT_PROMPT"}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": "blurry", "clip": ["9", 0]},
              "_meta": {"title": "INPUT_NEGATIVE"}},
        "4": {"class_type": "LTXVImgToVideo",
              "inputs": {"width": 768, "height": 512, "length": 97, "image": ["1", 0]},
              "_meta": {"title": "INPUT_SIZE"}},
        "5": {"class_type": "RandomNoise", "inputs": {"noise_seed": 1}, "_meta": {"title": "INPUT_SEED"}},
        "6": {"class_type": "LTXVConditioning", "inputs": {"frame_rate": 25, "positive": ["2", 0]},
              "_meta": {"title": "LTXVConditioning"}},
        "7": {"class_type": "SaveVideo", "inputs": {"video": ["8", 0]}, "_meta": {"title": "OUTPUT_VIDEO"}},
    }


def params(**overrides):
    base = dict(prompt="a mite walks", image_name="start.png", width=720, height=1280,
                length=145, fps=24, seed=42)
    base.update(overrides)
    return JobParams(**base)


class TestHelpers(unittest.TestCase):
    def test_frames_are_8n_plus_1(self):
        self.assertEqual(frames_for_seconds(6, 24), 145)
        self.assertEqual(frames_for_seconds(8, 24), 193)
        self.assertEqual((frames_for_seconds(7.3, 24) - 1) % 8, 0)

    def test_round_to_multiple(self):
        self.assertEqual(round_to_multiple(1080, 32, 256), 1088)
        self.assertEqual(round_to_multiple(10, 32, 256), 256)


class TestPatchWithTitles(unittest.TestCase):
    def test_all_fields(self):
        wf, report = patch_workflow(titled_workflow(), params(negative_prompt="ugly"))
        self.assertEqual(wf["2"]["inputs"]["text"], "a mite walks")
        self.assertEqual(wf["3"]["inputs"]["text"], "ugly")
        self.assertEqual(wf["1"]["inputs"]["image"], "start.png")
        self.assertEqual(wf["4"]["inputs"]["width"], 720)
        self.assertEqual(wf["4"]["inputs"]["height"], 1280)
        self.assertEqual(wf["4"]["inputs"]["length"], 145)
        self.assertEqual(wf["5"]["inputs"]["noise_seed"], 42)
        self.assertEqual(wf["6"]["inputs"]["frame_rate"], 24)
        self.assertEqual(wf["4"]["inputs"]["image"], ["1", 0])  # liens intacts
        self.assertIn("prompt", report.touched)

    def test_original_not_modified(self):
        original = titled_workflow()
        patch_workflow(original, params())
        self.assertEqual(original["2"]["inputs"]["text"], "old")

    def test_end_image(self):
        wf = titled_workflow()
        wf["10"] = {"class_type": "LoadImage", "inputs": {"image": "y.png"},
                    "_meta": {"title": "INPUT_END_IMAGE"}}
        out, _ = patch_workflow(wf, params(end_image_name="end.png"))
        self.assertEqual(out["10"]["inputs"]["image"], "end.png")
        self.assertEqual(out["1"]["inputs"]["image"], "start.png")


class TestPatchWithoutTitles(unittest.TestCase):
    def untitled(self):
        wf = titled_workflow()
        del wf["3"]  # un seul encodeur de texte
        for node in wf.values():
            node["_meta"]["title"] = node["class_type"]
        return wf

    def test_autodetect(self):
        wf, _ = patch_workflow(self.untitled(), params())
        self.assertEqual(wf["2"]["inputs"]["text"], "a mite walks")
        self.assertEqual(wf["1"]["inputs"]["image"], "start.png")
        self.assertEqual(wf["4"]["inputs"]["length"], 145)
        self.assertEqual(wf["5"]["inputs"]["noise_seed"], 42)

    def test_ambiguous_prompt_raises(self):
        wf = titled_workflow()
        for node in wf.values():
            node["_meta"]["title"] = node["class_type"]
        with self.assertRaises(WorkflowError):
            patch_workflow(wf, params())

    def test_end_image_needs_second_loader(self):
        with self.assertRaises(WorkflowError):
            patch_workflow(self.untitled(), params(end_image_name="end.png"))

    def test_ui_format_rejected(self):
        with self.assertRaises(WorkflowError):
            patch_workflow({"nodes": [], "links": []}, params())


class TestFindVideo(unittest.TestCase):
    def test_prefers_output_node(self):
        entry = {"outputs": {
            "3": {"images": [{"filename": "preview.png"}]},
            "9": {"images": [{"filename": "other.mp4", "subfolder": "", "type": "output"}]},
            "7": {"images": [{"filename": "final.mp4", "subfolder": "video", "type": "output"}],
                  "animated": [True]},
        }}
        found = find_video_outputs(entry, ["7"])
        self.assertEqual(found[0]["filename"], "final.mp4")
        self.assertEqual(len(found), 2)

    def test_none(self):
        self.assertEqual(find_video_outputs({"outputs": {}}), [])


if __name__ == "__main__":
    unittest.main()
