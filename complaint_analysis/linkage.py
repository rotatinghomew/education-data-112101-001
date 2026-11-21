"""关联线索生成（机器建议层）。

重要边界：本模块只产出"线索"（clue），不产出案件事实。每条线索都携带
规则名、匹配键、时间依据、证据依据与人工决定依据，工作人员可据此研判。

关联只使用稳定标识（household_ref/merchant_ref/course_ref/order_id/
session_ref/证据值），绝不按名称相似性合并，避免把不同机构错误并案。

线索规则：
1. same_household_topic 同一家庭在时间窗内就同一经营主体+课程的多次投诉；
   "补充说明"类记录作为补充成员挂接，不单独成案、不重复计数。
2. cross_course_sales    同一家庭在时间窗内跨课程/经营主体的多起投诉，
   用于识别跨课程销售链条。
3. shared_session        多条投诉指向同一直播场次（显式 session_ref，或
   在场次开始时间容差内按订单/投诉时间推断）。
4. shared_order          多条非补充投诉匹配到同一订单（收款主体线索）。
5. recurring_script      不同家庭的生效销售话术证据文本一致、时间接近，
   提示重复使用的销售话术。

输入更正的处理：线索每次运行都从当前资料重算。因当事人更正或证据补正
而失去成立条件的旧分组，以 retracted 状态保留并注明更正依据；人工决定
本身不会被删除，确认/排除判断继续挂在线索与案件上。
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import timedelta

from .evidence import active_sales_quotes, build_supersede_map, effective_households
from .matching import match_order
from .models import (
    AnalysisConfig,
    Clue,
    Complaint,
    Dataset,
    DecisionAction,
    ReasonItem,
    StaffDecision,
    SUPPLEMENT_KINDS,
    ClueState,
)


def _clue_id(rule: str, key: str, members: tuple[str, ...]) -> str:
    digest = hashlib.sha1()
    digest.update(rule.encode("utf-8"))
    digest.update(b"|")
    digest.update(key.encode("utf-8"))
    digest.update(b"|")
    digest.update(",".join(sorted(members)).encode("utf-8"))
    return f"L-{digest.hexdigest()[:10]}"


def _decisions_by_complaint(dataset: Dataset) -> dict[str, list[StaffDecision]]:
    index: dict[str, list[StaffDecision]] = defaultdict(list)
    for dec in dataset.decisions:
        index[dec.complaint_id].append(dec)
    return dict(index)


def _state_from_decisions(
    core_members: tuple[str, ...],
    decision_index: dict[str, list[StaffDecision]],
) -> tuple[str, list[StaffDecision]]:
    """依据成员上的人工决定判定线索状态。

    - 任一成员存在 merge_review -> confirmed（人工确认关联）；
    - 存在 keep_separate 且核心成员不少于 2 条 -> excluded（人工排除）；
    - 其余 -> candidate（仍待研判）。
    needs_documents 不改变研判状态，只作为依据保留。
    """

    attached: list[StaffDecision] = []
    for cid in core_members:
        attached.extend(decision_index.get(cid, ()))
    actions = {d.action for d in attached}
    if DecisionAction.MERGE_REVIEW in actions:
        return ClueState.CONFIRMED, attached
    if DecisionAction.KEEP_SEPARATE in actions and len(core_members) >= 2:
        return ClueState.EXCLUDED, attached
    return ClueState.CANDIDATE, attached


def _span_days(complaints: list[Complaint]) -> int | None:
    times = [c.submitted_at for c in complaints if c.submitted_at is not None]
    if len(times) < 2:
        return None
    return int((max(times) - min(times)).total_seconds() // 86400)


def build_clues(dataset: Dataset, config: AnalysisConfig) -> list[Clue]:
    decision_index = _decisions_by_complaint(dataset)
    effective, corrections = effective_households(dataset)
    complaints_by_id = {c.complaint_id: c for c in dataset.complaints}

    clues: list[Clue] = []
    clues.extend(_same_household_topic(dataset, config, effective, corrections, decision_index))
    clues.extend(_cross_course_sales(dataset, config, effective, corrections, decision_index))
    clues.extend(_shared_session(dataset, config, effective, decision_index))
    clues.extend(_shared_order(dataset, config, effective, decision_index))
    clues.extend(_recurring_script(dataset, config, effective, decision_index))
    clues.extend(
        _retracted_by_household_correction(
            dataset, config, effective, corrections, decision_index, complaints_by_id
        )
    )
    clues.extend(
        _retracted_cross_course_by_correction(
            dataset, config, effective, corrections, decision_index, clues
        )
    )

    # 去重：同一规则+成员只保留一条（不同分组路径可能重合），依据合并。
    deduped: dict[str, Clue] = {}
    for clue in clues:
        if clue.clue_id in deduped:
            existing = deduped[clue.clue_id]
            existing.reasons.extend(clue.reasons)
            seen = {d.decision_id for d in existing.decisions}
            for dec in clue.decisions:
                if dec.decision_id not in seen:
                    existing.decisions.append(dec)
        else:
            deduped[clue.clue_id] = clue
    return sorted(deduped.values(), key=lambda c: (c.rule, c.key, c.clue_id))


def _correction_reason(complaint_id: str, corrections: dict[str, list]) -> ReasonItem | None:
    chain = corrections.get(complaint_id)
    if not chain:
        return None
    latest = sorted(chain, key=lambda e: (e.recorded_at or e.recorded_at.min, e.evidence_id))[-1]
    return ReasonItem(
        rule="household_correction",
        detail=(
            f"投诉 {complaint_id} 的当事人归属经证据 {latest.evidence_id} 更正为 {latest.value}，"
            "关联按更正后的归属计算"
        ),
        evidence_ids=tuple(e.evidence_id for e in chain),
    )


def _same_household_topic(
    dataset: Dataset,
    config: AnalysisConfig,
    effective: dict[str, str],
    corrections: dict[str, list],
    decision_index: dict[str, list[StaffDecision]],
) -> list[Clue]:
    window = timedelta(days=config.same_topic_days)
    core_groups: dict[tuple, list[Complaint]] = defaultdict(list)
    supplements: dict[tuple, list[Complaint]] = defaultdict(list)

    for complaint in dataset.complaints:
        household = effective[complaint.complaint_id]
        if not (household and complaint.merchant_ref and complaint.course_ref):
            continue
        key = (household, complaint.merchant_ref, complaint.course_ref)
        if complaint.kind in SUPPLEMENT_KINDS:
            supplements[key].append(complaint)
        else:
            core_groups[key].append(complaint)

    clues: list[Clue] = []
    for key, core in core_groups.items():
        core_sorted = sorted(core, key=lambda c: (c.submitted_at or c.submitted_at.min, c.complaint_id))
        span = max(c.submitted_at for c in core_sorted if c.submitted_at) - min(
            c.submitted_at for c in core_sorted if c.submitted_at
        ) if len([c for c in core_sorted if c.submitted_at]) >= 2 else timedelta(0)
        if span > window:
            continue

        attached_supplements = [
            s
            for s in supplements.get(key, [])
            if s.submitted_at is None
            or any(
                c.submitted_at is not None and abs(s.submitted_at - c.submitted_at) <= window
                for c in core_sorted
            )
        ]
        # 至少两条核心投诉，或一条核心投诉带有补充材料，才构成可研判线索。
        if len(core_sorted) < 2 and not attached_supplements:
            continue
        all_members = tuple(
            sorted(c.complaint_id for c in core_sorted)
            + sorted(s.complaint_id for s in attached_supplements)
        )
        core_ids = tuple(c.complaint_id for c in core_sorted)
        state, attached_decisions = _state_from_decisions(core_ids, decision_index)
        key_text = "/".join(key)
        reasons = [
            ReasonItem(
                rule="same_household_topic",
                detail=(
                    f"同一当事人 {key[0]} 就经营主体 {key[1]} 的课程 {key[2]} 在 "
                    f"{config.same_topic_days} 天窗口内提交 {len(core_ids)} 条投诉"
                    + (f"，时间跨度 {_span_days(core_sorted)} 天" if _span_days(core_sorted) is not None else "")
                    + (f"，另有 {len(attached_supplements)} 条补充材料挂接" if attached_supplements else "")
                ),
            )
        ]
        for complaint in core_sorted + attached_supplements:
            reason = _correction_reason(complaint.complaint_id, corrections)
            if reason:
                reasons.append(reason)
        for sup in attached_supplements:
            reasons.append(
                ReasonItem(
                    rule="supplement_attachment",
                    detail=(
                        f"投诉 {sup.complaint_id} 类型为“{sup.kind}”，是同一家庭对既有投诉的补充，"
                        "挂接为补充成员，不单独计为一起案件"
                    ),
                )
            )
        clues.append(
            Clue(
                clue_id=_clue_id("same_household_topic", key_text, all_members),
                rule="same_household_topic",
                members=all_members,
                key=key_text,
                state=state,
                reasons=reasons,
                decisions=attached_decisions,
                is_supplement_members=tuple(s.complaint_id for s in attached_supplements),
            )
        )
    return clues


def _cross_course_sales(
    dataset: Dataset,
    config: AnalysisConfig,
    effective: dict[str, str],
    corrections: dict[str, list],
    decision_index: dict[str, list[StaffDecision]],
) -> list[Clue]:
    window = timedelta(days=config.cross_course_days)
    by_household: dict[str, list[Complaint]] = defaultdict(list)
    for complaint in dataset.complaints:
        if complaint.kind in SUPPLEMENT_KINDS:
            continue  # 补充说明不独立参与跨课程链条
        household = effective[complaint.complaint_id]
        if household:
            by_household[household].append(complaint)

    clues: list[Clue] = []
    for household, group in by_household.items():
        purchase_keys = {(c.merchant_ref, c.course_ref) for c in group if c.merchant_ref or c.course_ref}
        if len(purchase_keys) < 2:
            continue
        timed = [c for c in group if c.submitted_at is not None]
        if len(timed) < 2:
            continue
        timed.sort(key=lambda c: (c.submitted_at, c.complaint_id))
        if timed[-1].submitted_at - timed[0].submitted_at > window:
            continue
        members = tuple(sorted(c.complaint_id for c in group))
        state, attached_decisions = _state_from_decisions(members, decision_index)
        key_text = f"household={household}"
        reasons = [
            ReasonItem(
                rule="cross_course_sales",
                detail=(
                    f"当事人 {household} 在 {config.cross_course_days} 天窗口内涉及 "
                    f"{len(purchase_keys)} 组不同的经营主体/课程: "
                    + "；".join(f"{m or '未知主体'}/{c or '未知课程'}" for m, c in sorted(purchase_keys))
                ),
            )
        ]
        for complaint in group:
            reason = _correction_reason(complaint.complaint_id, corrections)
            if reason:
                reasons.append(reason)
        clues.append(
            Clue(
                clue_id=_clue_id("cross_course_sales", key_text, members),
                rule="cross_course_sales",
                members=members,
                key=key_text,
                state=state,
                reasons=reasons,
                decisions=attached_decisions,
            )
        )
    return clues


def _shared_session(
    dataset: Dataset,
    config: AnalysisConfig,
    effective: dict[str, str],
    decision_index: dict[str, list[StaffDecision]],
) -> list[Clue]:
    tolerance = timedelta(minutes=config.session_minutes)
    # 投诉 -> 场次：优先显式 session_ref，否则按主体+课程与场次时间容差推断。
    session_by_id = {s.session_ref: s for s in dataset.sessions}
    grouped: dict[str, list[Complaint]] = defaultdict(list)
    inferred: set[str] = set()

    for complaint in dataset.complaints:
        sid = complaint.session_ref
        if sid and sid in session_by_id:
            grouped[sid].append(complaint)
            continue
        if sid and sid not in session_by_id:
            continue  # 加载校验已给出 warning
        if not dataset.sessions or complaint.submitted_at is None:
            continue
        for session in dataset.sessions:
            if session.started_at is None:
                continue
            if complaint.merchant_ref and session.merchant_ref and complaint.merchant_ref != session.merchant_ref:
                continue
            if complaint.course_ref and session.course_ref and complaint.course_ref != session.course_ref:
                continue
            if abs(complaint.submitted_at - session.started_at) <= tolerance:
                grouped[session.session_ref].append(complaint)
                inferred.add(complaint.complaint_id)
                break

    clues: list[Clue] = []
    for sid, members in grouped.items():
        core = [c for c in members if c.kind not in SUPPLEMENT_KINDS]
        if len(core) < 2:
            continue
        session = session_by_id[sid]
        core_ids = tuple(sorted(c.complaint_id for c in core))
        sup_ids = tuple(sorted(c.complaint_id for c in members if c.kind in SUPPLEMENT_KINDS))
        all_members = tuple(sorted(c.complaint_id for c in members))
        state, attached_decisions = _state_from_decisions(core_ids, decision_index)
        match_mode = "显式挂接" if not any(c.complaint_id in inferred for c in members) else "时间容差推断"
        reasons = [
            ReasonItem(
                rule="shared_session",
                detail=(
                    f"{len(core_ids)} 条投诉指向同一直播场次 {sid}"
                    f"（经营主体 {session.merchant_ref or '未知'}，主播/账号 {session.host_ref or '未知'}，"
                    f"匹配方式: {match_mode}，容差 {config.session_minutes} 分钟）"
                ),
            )
        ]
        clues.append(
            Clue(
                clue_id=_clue_id("shared_session", f"session={sid}", all_members),
                rule="shared_session",
                members=all_members,
                key=f"session={sid}",
                state=state,
                reasons=reasons,
                decisions=attached_decisions,
                is_supplement_members=sup_ids,
            )
        )
    return clues


def _match_order(complaint: Complaint, dataset: Dataset, config: AnalysisConfig) -> str | None:
    """按稳定标识与时间邻近把投诉匹配到订单，返回 order_id（口径见 matching 模块）。"""

    order = match_order(complaint, dataset, config)
    return order.order_id if order else None


def _shared_order(
    dataset: Dataset,
    config: AnalysisConfig,
    effective: dict[str, str],
    decision_index: dict[str, list[StaffDecision]],
) -> list[Clue]:
    grouped: dict[str, list[Complaint]] = defaultdict(list)
    for complaint in dataset.complaints:
        order_id = _match_order(complaint, dataset, config)
        if order_id:
            grouped[order_id].append(complaint)

    clues: list[Clue] = []
    for order_id, members in grouped.items():
        core = [c for c in members if c.kind not in SUPPLEMENT_KINDS]
        if len(core) < 2:
            continue
        order = next(o for o in dataset.orders if o.order_id == order_id)
        core_ids = tuple(sorted(c.complaint_id for c in core))
        sup_ids = tuple(sorted(c.complaint_id for c in members if c.kind in SUPPLEMENT_KINDS))
        all_members = tuple(sorted(c.complaint_id for c in members))
        state, attached_decisions = _state_from_decisions(core_ids, decision_index)
        reasons = [
            ReasonItem(
                rule="shared_order",
                detail=(
                    f"{len(core_ids)} 条非补充投诉匹配到同一订单 {order_id}"
                    f"（收款经营主体 {order.merchant_ref or '未知'}，课程 {order.course_ref or '未知'}，"
                    f"金额 {order.amount if order.amount is not None else '未提供'}，"
                    f"退款状态 {order.refund_state}）"
                ),
            )
        ]
        clues.append(
            Clue(
                clue_id=_clue_id("shared_order", f"order={order_id}", all_members),
                rule="shared_order",
                members=all_members,
                key=f"order={order_id}",
                state=state,
                reasons=reasons,
                decisions=attached_decisions,
                is_supplement_members=sup_ids,
            )
        )
    return clues


def _recurring_script(
    dataset: Dataset,
    config: AnalysisConfig,
    effective: dict[str, str],
    decision_index: dict[str, list[StaffDecision]],
) -> list[Clue]:
    window = timedelta(days=config.cross_course_days)
    quotes = active_sales_quotes(dataset)
    by_value: dict[str, list] = defaultdict(list)
    for complaint_id, records in quotes.items():
        for ev in records:
            if ev.value:
                by_value[ev.value].append(ev)

    # 用于识别"仅由已失效证据支撑"的旧话术分组（补正后应撤回）。
    superseded = build_supersede_map(dataset.evidence)
    all_by_value: dict[str, list] = defaultdict(list)
    for ev in dataset.evidence:
        if ev.fact_kind == "sales_quote" and ev.value:
            all_by_value[ev.value].append(ev)

    clues: list[Clue] = []
    active_values: set[str] = set()
    for value, records in by_value.items():
        complaint_ids = sorted({ev.complaint_id for ev in records})
        households = {effective.get(cid) for cid in complaint_ids if effective.get(cid)}
        if len(households) < 2:
            continue
        times = [ev.recorded_at for ev in records if ev.recorded_at is not None]
        if len(times) >= 2 and max(times) - min(times) > window:
            continue
        merchants = {
            next((c.merchant_ref for c in dataset.complaints if c.complaint_id == cid), None)
            for cid in complaint_ids
        }
        merchants.discard(None)
        members = tuple(complaint_ids)
        key_text = f"quote={value}"
        state, attached_decisions = _state_from_decisions(members, decision_index)
        reasons = [
            ReasonItem(
                rule="recurring_script",
                detail=(
                    f"销售话术“{value}”出现在 {len(households)} 个不同当事人的投诉证据中"
                    + (f"，涉及经营主体 {sorted(merchants)}" if merchants else "，经营主体资料未提供")
                ),
                evidence_ids=tuple(sorted(ev.evidence_id for ev in records)),
            )
        ]
        clues.append(
            Clue(
                clue_id=_clue_id("recurring_script", key_text, members),
                rule="recurring_script",
                members=members,
                key=key_text,
                state=state,
                reasons=reasons,
                decisions=attached_decisions,
            )
        )
        active_values.add(value)

    # 因证据补正而不再满足跨家庭门槛的旧话术分组：保留为 retracted 线索。
    # 涵盖两种情形：证据全部被补正，或部分补正后剩余生效证据只来自一个家庭。
    for value, records in all_by_value.items():
        if value in active_values:
            continue
        dead = [ev for ev in records if ev.evidence_id in superseded]
        if not dead:
            continue
        complaint_ids = tuple(sorted({ev.complaint_id for ev in records}))
        households = {effective.get(cid) for cid in complaint_ids if effective.get(cid)}
        if len(households) < 2:
            continue
        replacement_ids = tuple(sorted({superseded[ev.evidence_id] for ev in dead}))
        clues.append(
            Clue(
                clue_id=_clue_id("recurring_script_retracted", f"quote={value}", complaint_ids),
                rule="recurring_script",
                members=complaint_ids,
                key=f"quote={value}",
                state=ClueState.RETRACTED,
                reasons=[
                    ReasonItem(
                        rule="evidence_superseded",
                        detail=(
                            f"支撑话术“{value}”跨家庭复现的证据已被补正，关联线索撤回；"
                            f"替代证据: {', '.join(replacement_ids)}"
                        ),
                        evidence_ids=tuple(sorted(ev.evidence_id for ev in dead)) + replacement_ids,
                    )
                ],
            )
        )
    return clues


def _retracted_by_household_correction(
    dataset: Dataset,
    config: AnalysisConfig,
    effective: dict[str, str],
    corrections: dict[str, list],
    decision_index: dict[str, list[StaffDecision]],
    complaints_by_id: dict[str, Complaint],
) -> list[Clue]:
    """当事人更正导致旧分组解散时，以 retracted 保留旧线索及其人工依据。"""

    if not corrections:
        return []
    window = timedelta(days=config.same_topic_days)
    # 用原始 household_ref 复算更正前的同主题分组。
    original_groups: dict[tuple, list[Complaint]] = defaultdict(list)
    for complaint in dataset.complaints:
        if complaint.kind in SUPPLEMENT_KINDS:
            continue
        if complaint.household_ref and complaint.merchant_ref and complaint.course_ref:
            original_groups[(complaint.household_ref, complaint.merchant_ref, complaint.course_ref)].append(
                complaint
            )

    # 当前生效分组的成员签名，用于识别已解散的旧分组。
    active_member_sets: set[tuple[str, ...]] = set()
    current_groups: dict[tuple, list[Complaint]] = defaultdict(list)
    for complaint in dataset.complaints:
        if complaint.kind in SUPPLEMENT_KINDS:
            continue
        household = effective[complaint.complaint_id]
        if household and complaint.merchant_ref and complaint.course_ref:
            current_groups[(household, complaint.merchant_ref, complaint.course_ref)].append(complaint)
    for group in current_groups.values():
        if len(group) >= 2:
            active_member_sets.add(tuple(sorted(c.complaint_id for c in group)))

    clues: list[Clue] = []
    for key, group in original_groups.items():
        if len(group) < 2:
            continue
        timed = [c for c in group if c.submitted_at is not None]
        if len(timed) >= 2 and max(c.submitted_at for c in timed) - min(c.submitted_at for c in timed) > window:
            continue
        signature = tuple(sorted(c.complaint_id for c in group))
        if signature in active_member_sets:
            continue  # 更正后分组仍然成立，由常规规则产出
        moved = [c for c in group if corrections.get(c.complaint_id)]
        if not moved:
            continue
        members = signature
        key_text = "/".join(key)
        # 人工已有的确认/排除决定继续挂接，判断与依据不丢失。
        attached = [d for cid in members for d in decision_index.get(cid, ())]
        reasons = [
            ReasonItem(
                rule="household_correction_retraction",
                detail=(
                    f"更正前 {key[0]} 就 {key[1]}/{key[2]} 的关联分组因当事人更正解散，线索撤回；"
                    "更正前的人工研判决定仍保留在本记录与案件台账中"
                ),
                evidence_ids=tuple(
                    sorted(e.evidence_id for c in moved for e in corrections[c.complaint_id])
                ),
                decision_ids=tuple(d.decision_id for d in attached),
            )
        ]
        clues.append(
            Clue(
                clue_id=_clue_id("same_household_topic_retracted", key_text, members),
                rule="same_household_topic",
                members=members,
                key=key_text,
                state=ClueState.RETRACTED,
                reasons=reasons,
                decisions=attached,
            )
        )
    return clues


def _retracted_cross_course_by_correction(
    dataset: Dataset,
    config: AnalysisConfig,
    effective: dict[str, str],
    corrections: dict[str, list],
    decision_index: dict[str, list[StaffDecision]],
    active_clues: list[Clue],
) -> list[Clue]:
    """当事人更正使跨课程链条成员变化或解散时，保留更正前的旧线索。"""

    if not corrections:
        return []
    window = timedelta(days=config.cross_course_days)

    # 更正前：按原始 household_ref 复算跨课程分组。
    original_by_household: dict[str, list[Complaint]] = defaultdict(list)
    for complaint in dataset.complaints:
        if complaint.kind in SUPPLEMENT_KINDS or not complaint.household_ref:
            continue
        original_by_household[complaint.household_ref].append(complaint)

    # 更正后仍成立的跨课程线索成员签名（含同规则同成员的候选/确认线索）。
    active_signatures = {
        tuple(sorted(clue.members))
        for clue in active_clues
        if clue.rule == "cross_course_sales" and clue.state != ClueState.RETRACTED
    }

    clues: list[Clue] = []
    for household, group in original_by_household.items():
        purchase_keys = {(c.merchant_ref, c.course_ref) for c in group if c.merchant_ref or c.course_ref}
        if len(purchase_keys) < 2:
            continue
        timed = [c for c in group if c.submitted_at is not None]
        if len(timed) < 2 or timed[-1].submitted_at - timed[0].submitted_at > window:
            continue
        signature = tuple(sorted(c.complaint_id for c in group))
        if signature in active_signatures:
            continue  # 更正后成员未变化，常规规则已产出
        moved = [c for c in group if corrections.get(c.complaint_id)]
        if not moved:
            continue
        attached = [d for cid in signature for d in decision_index.get(cid, ())]
        current_bindings = {cid: effective.get(cid, cid) for cid in signature}
        clues.append(
            Clue(
                clue_id=_clue_id("cross_course_sales_retracted", f"household={household}", signature),
                rule="cross_course_sales",
                members=signature,
                key=f"household={household}",
                state=ClueState.RETRACTED,
                reasons=[
                    ReasonItem(
                        rule="household_correction_retraction",
                        detail=(
                            f"更正前当事人 {household} 的跨课程销售链条因当事人更正发生成员变化，旧线索撤回；"
                            f"更正后归属: {sorted(set(current_bindings.values()))}；"
                            "更正前的人工研判决定仍保留在本记录与案件台账中"
                        ),
                        evidence_ids=tuple(
                            sorted(e.evidence_id for c in moved for e in corrections[c.complaint_id])
                        ),
                        decision_ids=tuple(d.decision_id for d in attached),
                    )
                ],
                decisions=attached,
            )
        )
    return clues
