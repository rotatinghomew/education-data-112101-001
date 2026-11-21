"""命令行入口冒烟测试。"""

import io
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from complaint_analysis.cli import main  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
DATA = str(ROOT / "data")


class CliTests(unittest.TestCase):
    def _run(self, *argv):
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            code = main(["--data", DATA, *argv])
        self.assertEqual(code, 0)
        return buffer.getvalue()

    def test_validate_clues_cases_metrics(self):
        self.assertIn("校验通过", self._run("validate"))
        clues = self._run("clues")
        self.assertIn("关联线索报告", clues)
        cases = self._run("cases")
        self.assertIn("案件台账", cases)
        metrics = self._run("metrics")
        self.assertIn("投诉变化汇总", metrics)

    def test_json_output_is_parseable(self):
        import json

        payload = json.loads(self._run("--json", "metrics"))
        self.assertTrue(any(c["dimension"] == "overall" for c in payload["metrics"]))

    def test_trace_complaint_and_metric(self):
        import json

        complaint = json.loads(self._run("--json", "trace", "--complaint", "C-101"))
        self.assertEqual(complaint["complaint"]["complaint_id"], "C-101")
        self.assertTrue(complaint["clues"])
        metric = json.loads(self._run("trace", "--metric", "merchant:M-3"))
        self.assertEqual(metric["metric"]["dimension"], "merchant")
        self.assertTrue(metric["cases"])

    def test_trace_unknown_object_reports_error(self):
        payload = self._run("--json", "trace", "--complaint", "C-NOPE")
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
