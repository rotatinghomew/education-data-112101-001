"""证据补正链与当事人更正测试。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from complaint_analysis.evidence import (  # noqa: E402
    active_evidence,
    build_supersede_map,
    effective_households,
)
from tests.support import run_pipeline  # noqa: E402


BASE_ROWS = {
    "complaints.csv": [
        "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
        "C-2,H-02,A-2,M-1,K-1,2026-04-02T10:00:00,退款,open,S-2",
    ],
    "orders.csv": [],
    "case_decisions.csv": [],
}


class EvidenceTests(unittest.TestCase):
    def test_chained_supersession_resolves_to_latest(self):
        rows = {
            **BASE_ROWS,
            "evidence.csv": [
                "E-1,C-1,S-1,2026-04-01,sales_quote,旧话术,",
                "E-2,C-1,S-1,2026-04-03,sales_quote,中间话术,E-1",
                "E-3,C-1,S-1,2026-04-05,sales_quote,最新话术,E-2",
            ],
        }
        report, dataset, tmp = run_pipeline(rows)
        with tmp:
            mapping = build_supersede_map(dataset.evidence)
            self.assertEqual(mapping["E-1"], "E-3")
            self.assertEqual(mapping["E-2"], "E-3")
            active_ids = {e.evidence_id for e in active_evidence(dataset.evidence)}
            self.assertEqual(active_ids, {"E-3"})
            self.assertEqual(report.superseded_evidence["E-1"], "E-3")

    def test_household_correction_changes_effective_binding(self):
        rows = {
            **BASE_ROWS,
            "evidence.csv": [
                "E-1,C-2,S-2,2026-04-03,household_correction,H-01,",
            ],
        }
        report, dataset, tmp = run_pipeline(rows)
        with tmp:
            effective, applied = effective_households(dataset)
            self.assertEqual(effective["C-2"], "H-01")
            self.assertIn("E-1", [e.evidence_id for e in applied["C-2"]])
            self.assertEqual(report.effective_household["C-2"], "H-01")

    def test_latest_correction_wins_and_order_is_deterministic(self):
        rows = {
            **BASE_ROWS,
            "evidence.csv": [
                "E-1,C-2,S-2,2026-04-03,household_correction,H-01,",
                "E-2,C-2,S-2,2026-04-08,household_correction,H-09,",
            ],
        }
        report, dataset, tmp = run_pipeline(rows)
        with tmp:
            effective, applied = effective_households(dataset)
            self.assertEqual(effective["C-2"], "H-09")
            self.assertEqual(len(applied["C-2"]), 2)
            # 再跑一次结果一致（纯函数、可重复）。
            report2, _, tmp2 = run_pipeline(rows)
            with tmp2:
                self.assertEqual(report2.effective_household, report.effective_household)

    def test_superseded_sales_quote_retracts_recurring_script_clue(self):
        # 三个不同家庭出现相同话术 -> recurring_script 线索；
        # 其中一个家庭的证据被补正为不同话术后，只剩两个家庭时线索仍成立，
        # 当补正使一致话术仅剩一个家庭时，旧线索撤回且保留血缘。
        rows = {
            "complaints.csv": [
                "C-1,H-01,A-1,M-1,K-1,2026-04-01T10:00:00,退款,open,S-1",
                "C-2,H-02,A-2,M-2,K-2,2026-04-02T10:00:00,退款,open,S-2",
                "C-3,H-03,A-3,M-3,K-3,2026-04-03T10:00:00,退款,open,S-3",
            ],
            "orders.csv": [],
            "case_decisions.csv": [],
            "evidence.csv": [
                "E-1,C-1,S-1,2026-04-01,sales_quote,统一话术,",
                "E-2,C-2,S-2,2026-04-02,sales_quote,统一话术,",
                "E-4,C-3,S-3,2026-04-03,sales_quote,统一话术,",
            ],
        }
        report_before, _, tmp_before = run_pipeline(rows)
        with tmp_before:
            active = [c for c in report_before.clues if c.rule == "recurring_script" and c.state != "retracted"]
            self.assertEqual(len(active), 1)

        # C-2、C-3 的证据都被补正为各自不同的话术后，"统一话术"只剩 C-1 一个家庭。
        rows["evidence.csv"] += [
            "E-3,C-2,S-2,2026-04-05,sales_quote,补正话术乙,E-2",
            "E-5,C-3,S-3,2026-04-05,sales_quote,补正话术丙,E-4",
        ]
        report, _, tmp = run_pipeline(rows)
        with tmp:
            script_clues = [c for c in report.clues if c.rule == "recurring_script"]
            self.assertTrue(script_clues)
            self.assertTrue(all(c.state == "retracted" for c in script_clues))
            self.assertTrue(
                any("E-3" in r.evidence_ids or "E-5" in r.evidence_ids for c in script_clues for r in c.reasons)
            )


if __name__ == "__main__":
    unittest.main()
