"""关联线索规则测试：稳定标识关联、补充挂接、时间窗、人工研判状态。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.support import run_pipeline  # noqa: E402


def _states(report, rule):
    return {clue.key: clue.state for clue in report.clues if clue.rule == rule}


class SameHouseholdTopicTests(unittest.TestCase):
    def test_supplement_attached_not_counted_as_separate_case(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,续单压力,open,S-1",
                "C-2,H-01,A-1,M-1,K-1,2026-04-02T10:00:00,补充说明,open,S-2",
            ],
            "orders.csv": [],
            "evidence.csv": [],
            "case_decisions.csv": [],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            clue = next(c for c in report.clues if c.rule == "same_household_topic")
            self.assertEqual(clue.members, ("C-1", "C-2"))
            self.assertEqual(clue.is_supplement_members, ("C-2",))
            self.assertEqual(clue.state, "candidate")
            # 没有人工决定 -> 不产生任何案件。
            self.assertEqual(report.cases, [])

    def test_out_of_window_complaints_not_grouped(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
                "C-2,H-01,A-1,M-1,K-1,2026-04-20T10:00:00,退款,open,S-2",
            ],
            "orders.csv": [],
            "evidence.csv": [],
            "case_decisions.csv": [],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            self.assertFalse([c for c in report.clues if c.rule == "same_household_topic"])

    def test_name_similar_but_different_merchant_refs_never_merge(self):
        # 名称相似性不参与关联；不同 merchant_ref 即使课程相同也不并线。
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
                "C-2,H-02,A-2,M-9,K-1,2026-04-02T10:00:00,退款,open,S-2",
            ],
            "orders.csv": [],
            "evidence.csv": [],
            "case_decisions.csv": [],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            topic = [c for c in report.clues if c.rule == "same_household_topic"]
            self.assertEqual(topic, [])

    def test_keep_separate_marks_clue_excluded_but_is_retained(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
                "C-2,H-01,A-1,M-1,K-1,2026-04-02T10:00:00,退款,open,S-2",
            ],
            "orders.csv": [],
            "evidence.csv": [],
            "case_decisions.csv": [
                "D-1,C-1,keep_separate,2026-04-03,R-1,different_purchase",
            ],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            clue = next(c for c in report.clues if c.rule == "same_household_topic")
            self.assertEqual(clue.state, "excluded")
            self.assertEqual([d.decision_id for d in clue.decisions], ["D-1"])
            # 排除决定依然形成独立案件，且两个投诉各有依据（C-2 无决定则不单独成案）。
            solo = [c for c in report.cases if c.case_id.startswith("CASE-SOLO")]
            self.assertEqual(len(solo), 1)
            self.assertIn("C-1", solo[0].member_complaints)


class CrossCourseTests(unittest.TestCase):
    def test_cross_course_chain_detected(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,续单,open,S-1",
                "C-2,H-01,A-1,M-2,K-2,2026-04-10T10:00:00,退款,open,S-2",
            ],
            "orders.csv": [],
            "evidence.csv": [],
            "case_decisions.csv": [],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            clue = next(c for c in report.clues if c.rule == "cross_course_sales")
            self.assertEqual(set(clue.members), {"C-1", "C-2"})
            self.assertIn("2 组不同的经营主体/课程", clue.reasons[0].detail)

    def test_merge_review_confirms_clue_and_creates_case(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,续单,open,S-1",
                "C-2,H-01,A-1,M-2,K-2,2026-04-10T10:00:00,退款,open,S-2",
            ],
            "orders.csv": [],
            "evidence.csv": [],
            "case_decisions.csv": [
                "D-1,C-1,merge_review,2026-04-11,R-1,source_check",
            ],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            clue = next(c for c in report.clues if c.rule == "cross_course_sales")
            self.assertEqual(clue.state, "confirmed")
            merge_cases = [c for c in report.cases if c.case_id.startswith("CASE-MERGE")]
            self.assertEqual(len(merge_cases), 1)
            self.assertEqual(set(merge_cases[0].member_complaints), {"C-1", "C-2"})
            self.assertTrue(
                any(r.rule == "confirmed_clue" and "D-1" in r.decision_ids for r in merge_cases[0].basis)
            )


class CorrectionRetractionTests(unittest.TestCase):
    def test_household_correction_dissolves_old_group_and_keeps_decisions(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
                "C-2,H-01,A-1,M-1,K-1,2026-04-02T10:00:00,退款,open,S-2",
            ],
            "orders.csv": [],
            "evidence.csv": [
                "E-1,C-2,S-2,2026-04-03,household_correction,H-07,",
            ],
            "case_decisions.csv": [
                "D-1,C-1,keep_separate,2026-04-03,R-1,reviewed_before_correction",
            ],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            topic = [c for c in report.clues if c.rule == "same_household_topic"]
            retracted = [c for c in topic if c.state == "retracted"]
            self.assertEqual(len(retracted), 1)
            self.assertEqual(set(retracted[0].members), {"C-1", "C-2"})
            # 人工决定及依据不丢失。
            self.assertTrue(any(d.decision_id == "D-1" for d in retracted[0].decisions))
            reason = retracted[0].reasons[0]
            self.assertIn("E-1", reason.evidence_ids)
            # 更正后 C-2 归属 H-07，新分组不再把二者关联。
            active_topic = [c for c in topic if c.state != "retracted"]
            self.assertEqual(active_topic, [])
            # 已有人工决定形成的案件仍保留。
            self.assertTrue(any(c for c in report.cases if "C-1" in c.member_complaints))

    def test_cross_course_clue_retracted_when_correction_moves_member(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,续单,open,S-1",
                "C-2,H-01,A-1,M-2,K-2,2026-04-05T10:00:00,退款,open,S-2",
                "C-3,H-01,A-1,M-3,K-3,2026-04-09T10:00:00,服务范围,open,S-3",
            ],
            "orders.csv": [],
            "evidence.csv": [
                "E-1,C-3,S-3,2026-04-10,household_correction,H-09,",
            ],
            "case_decisions.csv": [
                "D-1,C-1,merge_review,2026-04-08,R-1,source_check",
            ],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            cross = [c for c in report.clues if c.rule == "cross_course_sales"]
            retracted = [c for c in cross if c.state == "retracted"]
            self.assertEqual(len(retracted), 1)
            self.assertEqual(set(retracted[0].members), {"C-1", "C-2", "C-3"})
            # 更正前的 merge_review 决定随撤回线索保留。
            self.assertTrue(any(d.decision_id == "D-1" for d in retracted[0].decisions))
            # 更正后 H-01 仅剩两条投诉但仍满足跨课程门槛 -> 新线索成员缩小。
            active = [c for c in cross if c.state != "retracted"]
            self.assertEqual(len(active), 1)
            self.assertEqual(set(active[0].members), {"C-1", "C-2"})
            self.assertEqual(active[0].state, "confirmed")


class SharedSessionAndOrderTests(unittest.TestCase):
    def test_shared_session_groups_complaints(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:30:00,退款,open,S-1,LIVE-1",
                "C-2,H-02,A-2,M-1,K-1,2026-04-01T11:00:00,退款,open,S-2,LIVE-1",
            ],
            "orders.csv": [],
            "evidence.csv": [],
            "case_decisions.csv": [],
            "sessions.csv": [
                "LIVE-1,M-1,K-1,2026-04-01T09:00:00,HOST-1",
            ],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            clue = next(c for c in report.clues if c.rule == "shared_session")
            self.assertEqual(set(clue.members), {"C-1", "C-2"})

    def test_shared_order_groups_non_supplement_complaints(self):
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
                "C-2,H-01,A-1,M-1,K-1,2026-04-02T10:00:00,服务范围,open,S-2",
            ],
            "orders.csv": [
                "O-1,H-01,M-1,K-1,2026-04-01,1000,none",
            ],
            "evidence.csv": [],
            "case_decisions.csv": [],
        }
        report, _, tmp = run_pipeline(rows)
        with tmp:
            clue = next(c for c in report.clues if c.rule == "shared_order")
            self.assertEqual(set(clue.members), {"C-1", "C-2"})
            self.assertIn("O-1", clue.reasons[0].detail)


if __name__ == "__main__":
    unittest.main()
