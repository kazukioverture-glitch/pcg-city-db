import json
import tempfile
import unittest
from pathlib import Path

from analysis.sources import append_source_analysis
from analysis.state import read_state, write_state


class SourceAnalysisTests(unittest.TestCase):
    def test_append_and_processed_immutability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "processed.json"
            write_state(output, {"schema_version": "1.2.1", "kind": "processed_sources", "records": []})
            record = {
                "source_id": "video-1", "source_type": "video", "url": "https://example.test/v",
                "content_sha256": None, "processed_at": "2026-10-07T01:00:00+09:00",
                "status": "processed", "snapshot_ids": [], "notes": None,
                "published_at": "2026-10-06T12:00:00+09:00", "target_week": "W02",
                "language": "ja", "translation_used": False, "video_visuals_verified": False,
                "confirmed_facts": ["数字A"], "author_analysis": ["考察B"],
                "analysis_basis": ["根拠C"], "predictions": [], "caveats": ["映像未確認"],
                "tracking_signals": ["次週確認"],
            }
            path = root / "record.json"
            path.write_text(json.dumps(record, ensure_ascii=False))
            result = append_source_analysis(path, output)
            self.assertEqual(result["record_count"], 1)
            with self.assertRaisesRegex(ValueError, "already exists"):
                append_source_analysis(path, output)
            document = read_state(output)
            document["records"][0]["author_analysis"] = ["改変"]
            with self.assertRaisesRegex(ValueError, "immutable"):
                write_state(output, document)


if __name__ == "__main__":
    unittest.main()
