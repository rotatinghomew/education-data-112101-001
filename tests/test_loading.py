"""资料加载与校验测试：空字段语义、引用完整性、重复标识。"""

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from complaint_analysis.loading import load_dataset  # noqa: E402
from tests.support import write_dataset  # noqa: E402


class LoadingTests(unittest.TestCase):
    def test_empty_field_means_missing_not_zero(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_dataset(
                tmp,
                {
                    "complaints.csv": [
                        "C-1,H-01,,,K-9,2026-04-01T10:00:00,退款,open,S-1",
                    ],
                    "orders.csv": [
                        "O-1,H-01,,K-9,2026-04-01,,none",
                    ],
                    "evidence.csv": [],
                    "case_decisions.csv": [],
                },
            )
            dataset, issues = load_dataset(tmp)
        self.assertEqual(dataset.complaints[0].merchant_ref, None)
        self.assertEqual(dataset.orders[0].amount, None)
        self.assertEqual(dataset.orders[0].merchant_ref, None)
        self.assertFalse(any(i.level == "error" for i in issues))

    def test_dangling_evidence_reference_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_dataset(
                tmp,
                {
                    "complaints.csv": [
                        "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
                    ],
                    "orders.csv": [],
                    "evidence.csv": [
                        "E-1,C-999,S-1,2026-04-01,order,材料,",
                    ],
                    "case_decisions.csv": [],
                },
            )
            _, issues = load_dataset(tmp)
        self.assertTrue(any(i.code == "dangling_reference" and i.level == "error" for i in issues))

    def test_supersede_unknown_evidence_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_dataset(
                tmp,
                {
                    "complaints.csv": [
                        "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
                    ],
                    "orders.csv": [],
                    "evidence.csv": [
                        "E-1,C-1,S-1,2026-04-01,order,材料,E-9",
                    ],
                    "case_decisions.csv": [],
                },
            )
            _, issues = load_dataset(tmp)
        self.assertTrue(any(i.code == "dangling_reference" for i in issues))

    def test_duplicate_ids_are_reported(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_dataset(
                tmp,
                {
                    "complaints.csv": [
                        "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
                        "C-1,H-02,A-2,M-2,K-2,2026-04-02T10:00:00,退款,open,S-2",
                    ],
                    "orders.csv": [],
                    "evidence.csv": [],
                    "case_decisions.csv": [],
                },
            )
            _, issues = load_dataset(tmp)
        self.assertTrue(any(i.code == "duplicate_id" and i.level == "error" for i in issues))

    def test_unknown_decision_action_is_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_dataset(
                tmp,
                {
                    "complaints.csv": [
                        "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
                    ],
                    "orders.csv": [],
                    "evidence.csv": [],
                    "case_decisions.csv": [
                        "D-1,C-1,explode,2026-04-02,R-1,bad",
                    ],
                },
            )
            _, issues = load_dataset(tmp)
        self.assertTrue(any(i.code == "unknown_action" for i in issues))

    def test_optional_sessions_file_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_dataset(
                tmp,
                {
                    "complaints.csv": [
                        "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1,LIVE-1",
                    ],
                    "orders.csv": [],
                    "evidence.csv": [],
                    "case_decisions.csv": [],
                    "sessions.csv": [
                        "LIVE-1,M-1,K-1,2026-04-01T09:00:00,HOST-7",
                    ],
                },
            )
            # session_ref 是可选列，需要额外写入列头。
            path = Path(tmp) / "complaints.csv"
            path.write_text(
                "complaint_id,household_ref,account_ref,merchant_ref,course_ref,submitted_at,kind,status,source_ref,session_ref\n"
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1,LIVE-1\n",
                encoding="utf-8",
            )
            dataset, issues = load_dataset(tmp)
        self.assertEqual(len(dataset.sessions), 1)
        self.assertEqual(dataset.complaints[0].session_ref, "LIVE-1")
        self.assertFalse(any(i.level == "error" for i in issues))


if __name__ == "__main__":
    unittest.main()
