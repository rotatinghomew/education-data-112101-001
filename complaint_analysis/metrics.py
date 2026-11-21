"""汇总指标：按时间、课程、经营主体查看投诉变化。

与孤立汇总表的区别：
- 每个指标单元格（MetricCell）携带完整的投诉、案件、决定 ID 列表，
  可直接下钻到原始记录与处置依据；
- 可计入口径显式化：撤回投诉、补充类投诉不计入独立投诉数；全额退款
  订单单独计数；案件状态只来自工作人员决定形成的案件；
- 全部结果由同一套输入重算，输入更正后所有维度一致更新。
"""

from __future__ import annotations

from collections import defaultdict

from .matching import orders_by_complaint
from .models import (
    AnalysisConfig,
    CaseRecord,
    Complaint,
    ComplaintStatus,
    Dataset,
    MetricCell,
    RefundState,
    StaffDecision,
    SUPPLEMENT_KINDS,
)

UNKNOWN_KEY = "未提供"


def is_eligible(complaint: Complaint) -> bool:
    """该投诉是否可计入独立投诉统计。"""

    return complaint.status != ComplaintStatus.WITHDRAWN and complaint.kind not in SUPPLEMENT_KINDS


def _month_key(complaint: Complaint) -> str:
    if complaint.submitted_at is None:
        return UNKNOWN_KEY
    return complaint.submitted_at.strftime("%Y-%m")


def build_metrics(
    dataset: Dataset,
    cases: list[CaseRecord],
    config: AnalysisConfig,
) -> list[MetricCell]:
    order_map = orders_by_complaint(dataset, config)
    decision_index: dict[str, list[StaffDecision]] = defaultdict(list)
    for dec in dataset.decisions:
        decision_index[dec.complaint_id].append(dec)

    # 投诉 -> 案件（补充成员也建立指向，保证追溯完整）。
    complaint_case: dict[str, str] = {}
    for case in cases:
        for cid in case.member_complaints:
            complaint_case[cid] = case.case_id

    def dimension_key(complaint: Complaint, dimension: str) -> str:
        if dimension == "time":
            return _month_key(complaint)
        if dimension == "course":
            return complaint.course_ref or UNKNOWN_KEY
        if dimension == "merchant":
            return complaint.merchant_ref or UNKNOWN_KEY
        raise ValueError(f"未知统计维度: {dimension}")

    cells: list[MetricCell] = []
    for dimension in ("time", "course", "merchant"):
        buckets: dict[str, list[Complaint]] = defaultdict(list)
        for complaint in dataset.complaints:
            buckets[dimension_key(complaint, dimension)].append(complaint)
        for key in sorted(buckets):
            group = sorted(buckets[key], key=lambda c: c.complaint_id)
            cells.append(
                _build_cell(dimension, key, group, cases, complaint_case, decision_index, order_map)
            )

    # 总体口径单元格，便于和分维度结果对账。
    cells.append(
        _build_cell(
            "overall",
            "ALL",
            sorted(dataset.complaints, key=lambda c: c.complaint_id),
            cases,
            complaint_case,
            decision_index,
            order_map,
        )
    )
    return cells


def _build_cell(
    dimension: str,
    key: str,
    group: list[Complaint],
    cases: list[CaseRecord],
    complaint_case: dict[str, str],
    decision_index: dict[str, list[StaffDecision]],
    order_map: dict,
) -> MetricCell:
    all_ids = tuple(c.complaint_id for c in group)
    eligible = tuple(c.complaint_id for c in group if is_eligible(c))
    withdrawn = tuple(c.complaint_id for c in group if c.status == ComplaintStatus.WITHDRAWN)
    supplements = tuple(c.complaint_id for c in group if c.kind in SUPPLEMENT_KINDS)
    refund_full = tuple(
        cid
        for cid in eligible
        if (order := order_map.get(cid)) is not None and order.refund_state == RefundState.FULL
    )

    case_ids = tuple(sorted({complaint_case[cid] for cid in all_ids if cid in complaint_case}))
    cases_by_id = {case.case_id: case for case in cases}
    touched_cases = [cases_by_id[cid] for cid in case_ids]
    active_cases = tuple(c.case_id for c in touched_cases if c.state == "active")
    awaiting_cases = tuple(c.case_id for c in touched_cases if c.state == "awaiting_documents")
    closed_cases = tuple(
        c.case_id for c in touched_cases if c.state in ("closed_withdrawn", "resolved_refunded")
    )
    decision_ids = tuple(
        sorted(
            {
                dec.decision_id
                for cid in all_ids
                for dec in decision_index.get(cid, ())
            }
        )
    )

    return MetricCell(
        dimension=dimension,
        key=key,
        submitted_total=len(all_ids),
        eligible_count=len(eligible),
        withdrawn_count=len(withdrawn),
        supplement_count=len(supplements),
        refund_full_count=len(refund_full),
        active_case_count=len(active_cases),
        awaiting_documents_count=len(awaiting_cases),
        closed_case_count=len(closed_cases),
        complaint_ids=all_ids,
        eligible_complaint_ids=eligible,
        withdrawn_complaint_ids=withdrawn,
        supplement_complaint_ids=supplements,
        case_ids=case_ids,
        decision_ids=decision_ids,
    )
