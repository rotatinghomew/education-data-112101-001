"""案件层：把工作人员的处置决定落实为案件台账。

关键边界：
- 机器线索（candidate）不会自动变成案件；没有任何人工决定覆盖的投诉，
  只会在线索报告里出现，不进入案件汇总；
- 人工的 keep_separate / merge_review / needs_documents / open_case / split
  决定及其 reason_code 完整保留为案件依据；
- 输入更正后线索可能撤回或变化，但已确认案件不被机器删除，其依据链保留；
- 案件状态由投诉状态与订单退款状态推导，推导过程同样可追溯。
"""

from __future__ import annotations

import hashlib
from collections import defaultdict

from .evidence import effective_households
from .matching import orders_by_complaint
from .models import (
    AnalysisConfig,
    CaseRecord,
    CaseState,
    Clue,
    Complaint,
    ComplaintStatus,
    Dataset,
    DecisionAction,
    ReasonItem,
    RefundState,
    StaffDecision,
    SUPPLEMENT_KINDS,
)


def _case_id(prefix: str, members: tuple[str, ...]) -> str:
    digest = hashlib.sha1(",".join(sorted(members)).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}-{digest}"


def _decision_index(dataset: Dataset) -> dict[str, list[StaffDecision]]:
    index: dict[str, list[StaffDecision]] = defaultdict(list)
    for dec in dataset.decisions:
        index[dec.complaint_id].append(dec)
    return dict(index)


def _case_state(
    members: tuple[str, ...],
    complaints_by_id: dict[str, Complaint],
    order_map: dict[str, object],
    decision_index: dict[str, list[StaffDecision]],
) -> tuple[str, list[ReasonItem]]:
    """推导案件状态，返回状态与推导依据。"""

    reasons: list[ReasonItem] = []
    complaints = [complaints_by_id[cid] for cid in members if cid in complaints_by_id]
    eligible = [c for c in complaints if c.status != ComplaintStatus.WITHDRAWN and c.kind not in SUPPLEMENT_KINDS]
    withdrawn = [c for c in complaints if c.status == ComplaintStatus.WITHDRAWN]

    needs_docs = any(
        d.action == DecisionAction.NEEDS_DOCUMENTS
        for c in complaints
        for d in decision_index.get(c.complaint_id, ())
    )
    if needs_docs and eligible:
        reasons.append(
            ReasonItem(
                rule="state_awaiting_documents",
                detail="存在 needs_documents 决定且仍有未撤回投诉，案件暂挂等待补充资料",
                decision_ids=tuple(
                    d.decision_id
                    for c in complaints
                    for d in decision_index.get(c.complaint_id, ())
                    if d.action == DecisionAction.NEEDS_DOCUMENTS
                ),
            )
        )
        return CaseState.AWAITING_DOCUMENTS, reasons

    if eligible and all(c.status == ComplaintStatus.WITHDRAWN for c in complaints if c.kind not in SUPPLEMENT_KINDS):
        # 全部非补充投诉撤回（eligible 为空的分支之一）
        pass

    if not eligible:
        reasons.append(
            ReasonItem(
                rule="state_closed_withdrawn",
                detail=(
                    "案件内非补充投诉均已撤回"
                    if withdrawn
                    else "案件内没有可计入的非补充投诉"
                ),
            )
        )
        return CaseState.CLOSED_WITHDRAWN, reasons

    # 退款口径：可计入成员的订单全部全额退款，才视为争议已解决。
    refund_states: list[str] = []
    missing_order: list[str] = []
    for complaint in eligible:
        order = order_map.get(complaint.complaint_id)
        if order is None:
            missing_order.append(complaint.complaint_id)
        else:
            refund_states.append(order.refund_state)
    if refund_states and not missing_order and all(s == RefundState.FULL for s in refund_states):
        reasons.append(
            ReasonItem(
                rule="state_resolved_refunded",
                detail="全部可计入投诉对应订单均为全额退款，争议视为已解决",
            )
        )
        return CaseState.RESOLVED_REFUNDED, reasons

    reasons.append(
        ReasonItem(
            rule="state_active",
            detail=(
                "仍有未撤回投诉需要处理"
                + (f"，其中 {len(missing_order)} 条暂未匹配到订单" if missing_order else "")
                + (
                    f"，退款状态分布: {sorted(set(refund_states))}"
                    if refund_states
                    else ""
                )
            ),
        )
    )
    return CaseState.ACTIVE, reasons


def build_cases(
    dataset: Dataset,
    clues: list[Clue],
    config: AnalysisConfig,
) -> list[CaseRecord]:
    decision_index = _decision_index(dataset)
    complaints_by_id = {c.complaint_id: c for c in dataset.complaints}
    effective, _ = effective_households(dataset)
    order_map = orders_by_complaint(dataset, config)

    cases: list[CaseRecord] = []
    assigned: set[str] = set()  # 已进入案件的非补充投诉

    # 1) 人工确认的关联线索 -> 合并案件（retracted 线索也保留，案件不被机器删除）。
    confirmed = [clue for clue in clues if clue.state == "confirmed"]
    for clue in confirmed:
        core = tuple(cid for cid in clue.members if complaints_by_id[cid].kind not in SUPPLEMENT_KINDS)
        if not core:
            continue
        members = tuple(sorted(core))
        case_id = _case_id("CASE-MERGE", members)
        state, state_reasons = _case_state(members, complaints_by_id, order_map, decision_index)
        basis = [
            ReasonItem(
                rule="confirmed_clue",
                detail=(
                    f"线索 {clue.clue_id}（规则 {clue.rule}，匹配键 {clue.key}）经人工 merge_review 确认合并；"
                    "机器关联本身不构成事实，以工作人员决定为准"
                ),
                decision_ids=tuple(d.decision_id for d in clue.decisions),
            ),
            *clue.reasons,
            *state_reasons,
        ]
        cases.append(
            _assemble_case(case_id, state, members, basis, clue.decisions, complaints_by_id, effective, order_map)
        )
        assigned.update(members)

    # 2) 其余带人工决定的投诉 -> 独立案件（keep_separate / open_case / split / needs_documents）。
    standalone_actions = {
        DecisionAction.KEEP_SEPARATE,
        DecisionAction.OPEN_CASE,
        DecisionAction.SPLIT,
        DecisionAction.NEEDS_DOCUMENTS,
    }
    for complaint in dataset.complaints:
        cid = complaint.complaint_id
        if cid in assigned or complaint.kind in SUPPLEMENT_KINDS:
            continue
        decisions = decision_index.get(cid, ())
        if not any(d.action in standalone_actions for d in decisions):
            continue
        members = (cid,)
        case_id = _case_id("CASE-SOLO", members)
        state, state_reasons = _case_state(members, complaints_by_id, order_map, decision_index)
        action = next(d.action for d in decisions if d.action in standalone_actions)
        basis = [
            ReasonItem(
                rule=f"staff_{action}",
                detail=(
                    f"工作人员对投诉 {cid} 作出 {action} 决定"
                    + (
                        f"（原因码: {'; '.join(sorted({d.reason_code for d in decisions if d.reason_code}))}）"
                        if any(d.reason_code for d in decisions)
                        else ""
                    )
                ),
                decision_ids=tuple(d.decision_id for d in decisions),
            ),
            *state_reasons,
        ]
        cases.append(
            _assemble_case(case_id, state, members, basis, list(decisions), complaints_by_id, effective, order_map)
        )
        assigned.add(cid)

    # 3) 补充类投诉挂接到同一生效家庭的案件，并体现在成员与血缘中。
    #    优先匹配同经营主体+课程的案件；多个候选时取投诉时间最接近者，
    #    保证一条补充投诉只进入一个案件，避免重复计数。
    cases_by_id = {case.case_id: case for case in cases}
    for complaint in dataset.complaints:
        if complaint.kind not in SUPPLEMENT_KINDS:
            continue
        cid = complaint.complaint_id
        household = effective[cid]
        same_household_cases = [case for case in cases if case.household_ref == household]
        if not same_household_cases:
            continue

        def topic_match(case: CaseRecord) -> bool:
            member_details = [
                complaints_by_id[member]
                for member in case.member_complaints
                if member in complaints_by_id
            ]
            return any(
                c.merchant_ref == complaint.merchant_ref and c.course_ref == complaint.course_ref
                for c in member_details
            )

        topical = [case for case in same_household_cases if topic_match(case)]
        candidates = topical or same_household_cases

        def proximity(case: CaseRecord) -> tuple:
            member_times = [
                complaints_by_id[m].submitted_at
                for m in case.member_complaints
                if m in complaints_by_id and complaints_by_id[m].submitted_at is not None
            ]
            if complaint.submitted_at is None or not member_times:
                return (1, case.case_id)
            nearest = min(abs(complaint.submitted_at - t) for t in member_times)
            return (0, nearest, case.case_id)

        target = min(candidates, key=proximity)
        target.member_complaints = tuple(sorted(set(target.member_complaints) | {cid}))
        target.basis.append(
            ReasonItem(
                rule="supplement_attachment",
                detail=(
                    f"补充类投诉 {cid} 挂接到同一当事人 {household} 的案件 {target.case_id}"
                    + ("（按经营主体+课程匹配）" if topical else "（同家庭无同主题案件，按当事人挂接）")
                    + "，不计入独立投诉数，避免把同一家庭的多次补充当成多起案件"
                ),
            )
        )

    return sorted(cases, key=lambda c: c.case_id)


def _assemble_case(
    case_id: str,
    state: str,
    members: tuple[str, ...],
    basis: list[ReasonItem],
    decisions: list[StaffDecision],
    complaints_by_id: dict[str, Complaint],
    effective: dict[str, str],
    order_map: dict[str, object],
) -> CaseRecord:
    complaints = [complaints_by_id[cid] for cid in members]
    eligible = tuple(
        c.complaint_id
        for c in complaints
        if c.status != ComplaintStatus.WITHDRAWN and c.kind not in SUPPLEMENT_KINDS
    )
    withdrawn = tuple(c.complaint_id for c in complaints if c.status == ComplaintStatus.WITHDRAWN)
    open_ids = tuple(
        c.complaint_id for c in complaints if c.status == ComplaintStatus.OPEN and c.kind not in SUPPLEMENT_KINDS
    )
    merchants = tuple(sorted({c.merchant_ref for c in complaints if c.merchant_ref}))
    courses = tuple(sorted({c.course_ref for c in complaints if c.course_ref}))
    refunds = tuple(
        sorted(
            {
                order_map[cid].refund_state
                for cid in eligible
                if cid in order_map
            }
        )
    )
    households = {effective.get(cid) for cid in members if effective.get(cid)}
    return CaseRecord(
        case_id=case_id,
        state=state,
        member_complaints=members,
        basis=basis,
        decisions=list(decisions),
        household_ref=next(iter(households), None),
        merchant_ref=merchants[0] if len(merchants) == 1 else None,
        course_refs=courses,
        open_complaint_ids=open_ids,
        withdrawn_complaint_ids=withdrawn,
        eligible_complaint_ids=eligible,
        refund_states=refunds,
    )
