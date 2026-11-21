"""案件台账测试：人工决定边界、撤回、退款状态、补充挂接。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.support import run_pipeline  # noqa: E402


class CaseBoundaryTests(unittest.TestCase):
    def test_machine_clue_alone_creates_no_case(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
                "C-2,H-01,A-1,M-2,K-2,2026-04-02T10:00:00,退款,open,S-2",
            ],
            "orders.csv": [],
            "evidence.csv": [],
            "case_decisions.csv": [],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            self.assertTrue(any(c.rule == "cross_course_sales" for c in report.clues))
            self.assertEqual(report.cases, [])

    def test_withdrawn_complaint_closes_case(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,withdrawn,S-1",
            ],
            "orders.csv": [],
            "evidence.csv": [],
            "case_decisions.csv": [
                "D-1,C-1,open_case,2026-04-02,R-1,proceed",
            ],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            self.assertEqual(len(report.cases), 1)
            self.assertEqual(report.cases[0].state, "closed_withdrawn")
            self.assertEqual(report.cases[0].eligible_complaint_ids, ())
            self.assertEqual(report.cases[0].withdrawn_complaint_ids, ("C-1",))

    def test_full_refund_resolves_case(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
            ],
            "orders.csv": [
                "O-1,H-01,M-1,K-1,2026-04-01,1000,full",
            ],
            "evidence.csv": [],
            "case_decisions.csv": [
                "D-1,C-1,open_case,2026-04-02,R-1,proceed",
            ],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            self.assertEqual(report.cases[0].state, "resolved_refunded")

    def test_partial_refund_keeps_case_active(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
            ],
            "orders.csv": [
                "O-1,H-01,M-1,K-1,2026-04-01,1000,partial",
            ],
            "evidence.csv": [],
            "case_decisions.csv": [
                "D-1,C-1,open_case,2026-04-02,R-1,proceed",
            ],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            self.assertEqual(report.cases[0].state, "active")

    def test_needs_documents_suspends_case(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
            ],
            "orders.csv": [],
            "evidence.csv": [],
            "case_decisions.csv": [
                "D-1,C-1,needs_documents,2026-04-02,R-1,missing_order",
            ],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            self.assertEqual(report.cases[0].state, "awaiting_documents")

    def test_supplement_never_becomes_case_member_count(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,续单压力,open,S-1",
                "C-2,H-01,A-1,M-1,K-1,2026-04-02T10:00:00,补充说明,open,S-2",
            ],
            "orders.csv": [],
            "evidence.csv": [],
            "case_decisions.csv": [
                "D-1,C-1,open_case,2026-04-03,R-1,proceed",
            ],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            case = report.cases[0]
            self.assertIn("C-2", case.member_complaints)  # 挂接可追溯
            self.assertNotIn("C-2", case.eligible_complaint_ids)  # 但不计入
            self.assertEqual(case.eligible_complaint_ids, ("C-1",))

    def test_split_decision_keeps_complaint_in_own_case(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
                "C-2,H-01,A-1,M-1,K-1,2026-04-02T10:00:00,退款,open,S-2",
            ],
            "orders.csv": [],
            "evidence.csv": [],
            "case_decisions.csv": [
                "D-1,C-1,split,2026-04-03,R-1,distinct_contract",
                "D-2,C-2,split,2026-04-03,R-1,distinct_contract",
            ],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            solo = sorted(report.cases, key=lambda c: c.case_id)
            self.assertEqual(len(solo), 2)
            self.assertEqual(solo[0].member_complaints, ("C-1",))
            self.assertEqual(solo[1].member_complaints, ("C-2",))
            self.assertTrue(all(any(r.rule == "staff_split" for r in c.basis) for c in solo))


if __name__ == "__main__":
    unittest.main()
