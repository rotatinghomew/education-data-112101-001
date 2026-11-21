"""汇总口径、血缘追溯与端到端一致性测试。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from complaint_analysis.lineage import trace_case, trace_complaint, trace_metric  # noqa: E402
from complaint_analysis.metrics import is_eligible  # noqa: E402
from complaint_analysis.pipeline import run  # noqa: E402
from tests.support import run_pipeline  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


class MetricsTests(unittest.TestCase):
    def _rows(self):
        return {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
                "C-2,H-01,A-1,M-1,K-1,2026-04-02T10:00:00,补充说明,open,S-2",
                "C-3,H-02,A-2,M-1,K-2,2026-04-03T10:00:00,退款,withdrawn,S-3",
                "C-4,H-03,A-3,M-2,K-1,2026-05-01T10:00:00,续单,open,S-4",
            ],
            "orders.csv": [
                "O-1,H-01,M-1,K-1,2026-04-01,1000,full",
            ],
            "evidence.csv": [],
            "case_decisions.csv": [
                "D-1,C-1,open_case,2026-04-04,R-1,proceed",
            ],
        }

    def test_eligibility_excludes_withdrawn_and_supplements(self):
        report, dataset, tmp = run_pipeline(self._rows())
        with tmp:
            statuses = {c.complaint_id: is_eligible(c) for c in dataset.complaints}
            self.assertTrue(statuses["C-1"])
            self.assertFalse(statuses["C-2"])
            self.assertFalse(statuses["C-3"])
            self.assertTrue(statuses["C-4"])

    def test_overall_cell_lineage_covers_all_records(self):
        report, _, tmp = run_pipeline(self._rows())
        with tmp:
            overall = next(c for c in report.metric_cells if c.dimension == "overall")
            self.assertEqual(overall.submitted_total, 4)
            self.assertEqual(overall.eligible_count, 2)
            self.assertEqual(overall.withdrawn_count, 1)
            self.assertEqual(overall.supplement_count, 1)
            self.assertEqual(overall.refund_full_count, 1)
            self.assertEqual(set(overall.complaint_ids), {"C-1", "C-2", "C-3", "C-4"})
            self.assertEqual(overall.decision_ids, ("D-1",))
            self.assertTrue(overall.case_ids)

    def test_time_dimension_buckets_by_month(self):
        report, _, tmp = run_pipeline(self._rows())
        with tmp:
            time_cells = {c.key: c for c in report.metric_cells if c.dimension == "time"}
            self.assertEqual(time_cells["2026-04"].eligible_count, 1)
            self.assertEqual(time_cells["2026-05"].eligible_count, 1)

    def test_metric_trace_returns_cases_and_derivation(self):
        report, dataset, tmp = run_pipeline(self._rows())
        with tmp:
            traced = trace_metric(report, "merchant", "M-1")
            self.assertNotIn("error", traced)
            self.assertEqual(set(traced["metric"]["complaint_ids"]), {"C-1", "C-2", "C-3"})
            self.assertTrue(traced["cases"])
            self.assertTrue(any(r["rule"] == "eligible_rule" for r in traced["derivation"]))

    def test_complaint_trace_includes_evidence_chain_and_case(self):
        report, dataset, tmp = run_pipeline(
            {
                "complaints.csv": [
                    "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
                ],
                "orders.csv": [
                    "O-1,H-01,M-1,K-1,2026-04-01,1000,none",
                ],
                "evidence.csv": [
                    "E-1,C-1,S-1,2026-04-01,sales_quote,旧,",
                    "E-2,C-1,S-1,2026-04-02,sales_quote,新,E-1",
                ],
                "case_decisions.csv": [
                    "D-1,C-1,open_case,2026-04-03,R-1,proceed",
                ],
            }
        )
        with tmp:
            traced = trace_complaint(report, dataset, "C-1")
            self.assertEqual(traced["case_id"], report.cases[0].case_id)
            evidence = {e["evidence_id"]: e for e in traced["evidence"]}
            self.assertEqual(evidence["E-1"]["status"], "superseded")
            self.assertEqual(evidence["E-1"]["replaced_by"], "E-2")
            self.assertEqual(evidence["E-2"]["status"], "active")
            self.assertEqual(traced["candidate_orders"][0]["order_id"], "O-1")
            self.assertEqual(traced["decisions"][0]["decision_id"], "D-1")

    def test_case_trace_round_trip(self):
        report, _, tmp = run_pipeline(self._rows())
        with tmp:
            case_id = report.cases[0].case_id
            traced = trace_case(report, case_id)
            self.assertEqual(traced["case"]["case_id"], case_id)


class DeterminismTests(unittest.TestCase):
    def test_repeated_runs_are_identical(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
                "C-2,H-01,A-1,M-1,K-1,2026-04-02T10:00:00,补充说明,open,S-2",
                "C-3,H-02,A-2,M-1,K-2,2026-04-03T10:00:00,退款,withdrawn,S-3",
                "C-4,H-03,A-3,M-2,K-1,2026-05-01T10:00:00,续单,open,S-4",
            ],
            "orders.csv": [
                "O-1,H-01,M-1,K-1,2026-04-01,1000,full",
            ],
            "evidence.csv": [],
            "case_decisions.csv": [
                "D-1,C-1,open_case,2026-04-04,R-1,proceed",
            ],
        }
        first, _, tmp1 = run_pipeline(rows)
        with tmp1:
            second, _, tmp2 = run_pipeline(rows)
            with tmp2:
                self.assertEqual(
                    [c.to_dict() for c in first.clues],
                    [c.to_dict() for c in second.clues],
                )
                self.assertEqual(
                    [c.to_dict() for c in first.cases],
                    [c.to_dict() for c in second.cases],
                )
                self.assertEqual(
                    [c.to_dict() for c in first.metric_cells],
                    [c.to_dict() for c in second.metric_cells],
                )
                self.assertEqual(first.input_fingerprint, second.input_fingerprint)


class FixtureEndToEndTests(unittest.TestCase):
    """对仓库内中文合成资料的端到端核对（数值由资料推导，锁定关键口径）。"""

    @classmethod
    def setUpClass(cls):
        cls.report, cls.dataset = run(ROOT / "data")

    def test_no_blocking_issues(self):
        errors = [i for i in self.report.issues if i.level == "error"]
        self.assertEqual(errors, [])

    def test_fixture_totals(self):
        overall = next(c for c in self.report.metric_cells if c.dimension == "overall")
        self.assertEqual(overall.submitted_total, 20)
        self.assertEqual(overall.eligible_count, 14)
        self.assertEqual(overall.withdrawn_count, 5)
        self.assertEqual(overall.supplement_count, 1)

    def test_seven_households_have_cross_course_clues(self):
        cross = [c for c in self.report.clues if c.rule == "cross_course_sales"]
        self.assertEqual(len(cross), 7)
        states = {c.key: c.state for c in cross}
        self.assertEqual(states["household=H-01"], "excluded")
        self.assertEqual(states["household=H-05"], "confirmed")

    def test_h01_supplement_is_attached_but_not_double_counted(self):
        topic = next(
            c
            for c in self.report.clues
            if c.rule == "same_household_topic" and set(c.members) == {"C-101", "C-102"}
        )
        self.assertEqual(topic.is_supplement_members, ("C-102",))
        overall = next(c for c in self.report.metric_cells if c.dimension == "overall")
        self.assertNotIn("C-102", overall.eligible_complaint_ids)

    def test_only_staff_decisions_create_cases(self):
        # 6 件案件 = 1 合并确认 + 5 个带决定的独立投诉；
        # 14 条可计入投诉中其余 8 条没有人工决定，不进案件台账。
        self.assertEqual(len(self.report.cases), 6)
        decided_complaints = {d.complaint_id for d in self.dataset.decisions}
        # 每件案件至少有一条人工决定作为依据；独立案件的主成员必须本人有决定。
        supplement_ids = {
            c.complaint_id for c in self.dataset.complaints if c.kind in {"补充说明"}
        }
        for case in self.report.cases:
            self.assertTrue(case.decisions)
            if case.case_id.startswith("CASE-SOLO"):
                principals = case.eligible_complaint_ids or tuple(
                    cid for cid in case.member_complaints if cid not in supplement_ids
                )
                self.assertTrue(principals)
                self.assertIn(principals[0], decided_complaints)
        # 没有任何人工决定覆盖的投诉不得出现在案件可计入成员中。
        solo_or_merge_principals = {
            cid
            for case in self.report.cases
            for cid in case.eligible_complaint_ids
        }
        undecided = {
            c.complaint_id
            for c in self.dataset.complaints
            if c.complaint_id not in decided_complaints
            and c.complaint_id in solo_or_merge_principals
        }
        # 合并案件允许成员经由同线索上的他人决定被带入（C-109 随 C-116 的 merge_review 合并）。
        merge_members = {
            cid for case in self.report.cases if case.case_id.startswith("CASE-MERGE") for cid in case.member_complaints
        }
        self.assertEqual(undecided - merge_members, set())

    def test_evidence_supersession_visible_in_report(self):
        self.assertEqual(self.report.superseded_evidence.get("E-1"), "E-3")

    def test_every_metric_cell_is_traceable(self):
        for cell in self.report.metric_cells:
            traced = trace_metric(self.report, cell.dimension, cell.key)
            self.assertNotIn("error", traced)
            self.assertEqual(traced["metric"]["submitted_total"], cell.submitted_total)


if __name__ == "__main__":
    unittest.main()
