"""Portable offline tests for the teacher's actual verified 120-trajectory package."""
import json
import unittest
from pathlib import Path
import e1_quickstart as q

class TeacherDeliveryTests(unittest.TestCase):
    def test_complete_data_and_source_paths(self):
        self.assertTrue(q.source_jsonl().is_file(), "Run setup first")
        by_dataset = {}
        families = set()
        total = 0
        native_images = 0
        with q.source_jsonl().open(encoding="utf-8") as f:
            for line in f:
                obj = json.loads(line)
                total += 1
                by_dataset[obj["dataset"]] = by_dataset.get(obj["dataset"], 0) + 1
                families.add(obj["family_id"])
                for e in obj["events"]:
                    for k in ("pre", "post"):
                        x = e[k]
                        if x["status"] == "PRESENT":
                            self.assertTrue(Path(x["path"]).is_file(), x["path"])
                            native_images += 1
        self.assertEqual(total, 120)
        self.assertEqual(len(families), 40)
        self.assertEqual(by_dataset, {"AgentNet":10, "OM2W":40, "OSWorld":60, "WebChain":10})
        self.assertGreater(native_images, 0)

    def test_codebook_and_prompts(self):
        for name in ("GOAL_CODEBOOK.md", "ALIGNMENT_CODEBOOK.md",
                     "OPERATOR_CODEBOOK.md", "prompts/goals.md",
                     "prompts/alignment.md", "prompts/operator.md", "schemas.json"):
            self.assertTrue((q.CODE / name).is_file(), name)
        cfg = json.loads((q.CODE / "config_api_template.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["model"]["capabilities"]["structured_output"], "json_schema")
        self.assertTrue(cfg["model"]["capabilities"]["images"])
        self.assertEqual(cfg["trajectory_input"]["window_units"], 10)
        self.assertEqual(cfg["trajectory_input"]["context_units"], 2)
        self.assertEqual(cfg["images"]["max_edge"], 1536)
        self.assertEqual(cfg["images"]["jpeg_quality"], 95)

    def test_package_manifest(self):
        data = q.WORK / "data"
        index = json.loads((data / "PACKAGE_INDEX.json").read_text(encoding="utf-8"))
        self.assertEqual(len(index["archives"]), 12)
        self.assertTrue((data / "SOURCE_PACKAGE_120" / "FILE_MANIFEST.jsonl").is_file())

if __name__ == "__main__":
    unittest.main()
