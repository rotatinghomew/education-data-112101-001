"""血缘追溯：把任意统计结果、案件或投诉回溯到原始记录与处置决定。

三个入口：
- trace_complaint: 一条投诉的完整来龙去脉（证据含补正链、订单、线索、案件、决定）；
- trace_case:       一个案件的成员、依据与状态推导；
- trace_metric:     一个统计单元格由哪些投诉、案件、决定构成。
"""

from __future__ import annotations

from .evidence import build_supersede_map
from .models import AnalysisReport, Clue, StaffDecision


def trace_complaint(report: AnalysisReport, dataset, complaint_id: str) -> dict:
    complaint = next((c for c in dataset.complaints if c.complaint_id == complaint_id), None)
    if complaint is None:
        return {"error": f"投诉 {complaint_id} 不存在"}

    superseded = build_supersede_map(dataset.evidence)
    own_evidence = [ev for ev in dataset.evidence if ev.complaint_id == complaint_id]
    evidence_view = []
    for ev in own_evidence:
        entry = {
            "evidence_id": ev.evidence_id,
            "fact_kind": ev.fact_kind,
            "value": ev.value,
            "recorded_at": ev.recorded_at.isoformat() if ev.recorded_at else None,
            "status": "superseded" if ev.evidence_id in superseded else "active",
        }
        if ev.evidence_id in superseded:
            entry["replaced_by"] = superseded[ev.evidence_id]
        if ev.supersedes:
            entry["supersedes"] = ev.supersedes
        evidence_view.append(entry)

    clue_ids = report.complaint_clue_index.get(complaint_id, [])
    case_id = report.complaint_case_index.get(complaint_id)
    decisions = [d for d in dataset.decisions if d.complaint_id == complaint_id]
    orders = [
        o
        for o in dataset.orders
        if o.household_ref == complaint.household_ref
        and (not o.merchant_ref or not complaint.merchant_ref or o.merchant_ref == complaint.merchant_ref)
        and (not o.course_ref or not complaint.course_ref or o.course_ref == complaint.course_ref)
    ]

    return {
        "complaint": {
            "complaint_id": complaint.complaint_id,
            "original_household_ref": complaint.household_ref,
            "effective_household_ref": report.effective_household.get(complaint_id, complaint.household_ref),
            "merchant_ref": complaint.merchant_ref,
            "course_ref": complaint.course_ref,
            "kind": complaint.kind,
            "status": complaint.status,
            "submitted_at": complaint.submitted_at.isoformat() if complaint.submitted_at else None,
            "source_ref": complaint.source_ref,
            "session_ref": complaint.session_ref,
        },
        "evidence": evidence_view,
        "candidate_orders": [
            {
                "order_id": o.order_id,
                "merchant_ref": o.merchant_ref,
                "course_ref": o.course_ref,
                "amount": o.amount,
                "refund_state": o.refund_state,
                "ordered_at": o.ordered_at.isoformat() if o.ordered_at else None,
            }
            for o in orders
        ],
        "clues": [_clue_brief(_find_clue(report, cid)) for cid in clue_ids if _find_clue(report, cid)],
        "case_id": case_id,
        "decisions": [_decision_dict(d) for d in decisions],
    }


def trace_case(report: AnalysisReport, case_id: str) -> dict:
    case = next((c for c in report.cases if c.case_id == case_id), None)
    if case is None:
        return {"error": f"案件 {case_id} 不存在"}
    return {
        "case": case.to_dict(),
        "clues": [
            _clue_brief(clue)
            for clue in report.clues
            if any(cid in case.member_complaints for cid in clue.members)
        ],
    }


def trace_metric(report: AnalysisReport, dimension: str, key: str) -> dict:
    cell = next(
        (c for c in report.metric_cells if c.dimension == dimension and c.key == key),
        None,
    )
    if cell is None:
        return {"error": f"统计维度 {dimension}/{key} 不存在"}
    return {
        "metric": cell.to_dict(),
        "derivation": [
            {"rule": "eligible_rule", "detail": "可计入 = 非撤回且非补充类投诉"},
            {"rule": "withdrawn_rule", "detail": "status=withdrawn 的投诉不计入独立投诉数"},
            {"rule": "supplement_rule", "detail": "补充说明类投诉挂接案件但不单独计数"},
            {"rule": "refund_rule", "detail": "refund_state=full 单独计数；案件仅在全部可计入成员全额退款时转为 resolved_refunded"},
            {"rule": "case_rule", "detail": "案件只由工作人员决定产生，机器 candidate 线索不计案件数"},
        ],
        "cases": [
            report_case.to_dict()
            for report_case in report.cases
            if report_case.case_id in cell.case_ids
        ],
    }


def _find_clue(report: AnalysisReport, clue_id: str) -> Clue | None:
    return next((c for c in report.clues if c.clue_id == clue_id), None)


def _clue_brief(clue: Clue) -> dict:
    return {
        "clue_id": clue.clue_id,
        "rule": clue.rule,
        "key": clue.key,
        "state": clue.state,
        "members": list(clue.members),
        "reasons": [
            {"rule": r.rule, "detail": r.detail, "evidence_ids": list(r.evidence_ids)}
            for r in clue.reasons
        ],
    }


def _decision_dict(decision: StaffDecision) -> dict:
    return {
        "decision_id": decision.decision_id,
        "action": decision.action,
        "reviewed_at": decision.reviewed_at.isoformat() if decision.reviewed_at else None,
        "reviewer_ref": decision.reviewer_ref,
        "reason_code": decision.reason_code,
    }
