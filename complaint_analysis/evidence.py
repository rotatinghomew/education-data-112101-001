"""证据层：补正链（supersede）与当事人更正的生效计算。

核心原则：
- 旧证据被新证据 supersedes 后不再参与线索研判，但记录保留，血缘里仍可追溯；
- `household_correction` 类证据修正当事人归属，后续所有关联按更正后的
  household_ref 计算，同时保留"原始归属 -> 更正归属"的依据链；
- 本模块只做事实层计算，不触碰工作人员的人工决定。
"""

from __future__ import annotations

from collections import defaultdict

from .models import Dataset, EvidenceRecord


def build_supersede_map(evidence: list[EvidenceRecord]) -> dict[str, str]:
    """返回 失效证据id -> 最终生效证据id 的映射。

    支持链式补正（A 被 B 补正、B 又被 C 补正，则 A、B 都指向 C）。
    若补正成环，则环上证据按未补正处理并交由加载校验/调用方发现。
    """

    direct: dict[str, str] = {}
    for ev in evidence:
        if ev.supersedes:
            direct[ev.supersedes] = ev.evidence_id

    resolved: dict[str, str] = {}
    for old_id in direct:
        seen: set[str] = set()
        current = old_id
        while current in direct and current not in seen:
            seen.add(current)
            current = direct[current]
        if current in seen:  # 成环：保持原样，不做错误指向
            continue
        resolved[old_id] = current
    return resolved


def active_evidence(evidence: list[EvidenceRecord]) -> list[EvidenceRecord]:
    """返回未被补正、当前仍生效的证据（按 recorded_at、id 排序）。"""

    superseded = build_supersede_map(evidence)
    return sorted(
        (ev for ev in evidence if ev.evidence_id not in superseded),
        key=lambda e: (e.recorded_at or e.recorded_at.min, e.evidence_id),
    )


def evidence_for_complaint(evidence: list[EvidenceRecord], complaint_id: str) -> list[EvidenceRecord]:
    """某条投诉当前生效的证据。"""

    return [ev for ev in active_evidence(evidence) if ev.complaint_id == complaint_id]


def effective_households(dataset: Dataset) -> tuple[dict[str, str], dict[str, list[EvidenceRecord]]]:
    """计算每条投诉更正后的当事人归属。

    返回：
    1. complaint_id -> 生效 household_ref（无更正时等于投诉原始值）；
    2. complaint_id -> 触发更正的证据列表（保留更正依据）。
    同一投诉存在多条更正证据时，以 recorded_at 最晚者为准；时间并列时按
    evidence_id 排序保证可重复。
    """

    corrections: dict[str, list[EvidenceRecord]] = defaultdict(list)
    for ev in active_evidence(dataset.evidence):
        if ev.fact_kind == "household_correction" and ev.value:
            corrections[ev.complaint_id].append(ev)

    effective: dict[str, str] = {}
    applied: dict[str, list[EvidenceRecord]] = {}
    for complaint in dataset.complaints:
        chain = corrections.get(complaint.complaint_id, [])
        if chain:
            latest = sorted(chain, key=lambda e: (e.recorded_at or e.recorded_at.min, e.evidence_id))[-1]
            effective[complaint.complaint_id] = latest.value
            applied[complaint.complaint_id] = chain
        else:
            effective[complaint.complaint_id] = complaint.household_ref
    return effective, applied


def active_sales_quotes(dataset: Dataset) -> dict[str, list[EvidenceRecord]]:
    """complaint_id -> 当前生效的销售话术类证据（用于复现话术关联）。"""

    result: dict[str, list[EvidenceRecord]] = defaultdict(list)
    for ev in active_evidence(dataset.evidence):
        if ev.fact_kind == "sales_quote":
            result[ev.complaint_id].append(ev)
    return dict(result)
