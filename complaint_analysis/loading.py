"""读取并校验合成 CSV 资料。

设计约定：
- 空字段读为 None，含义是"资料未提供"，不代表零或业务状态被否定；
- 校验问题分级收集（error/warning），不轻易抛异常中断，保证部分资料
  缺失时仍能产出可解释的结果，但 error 会在报告中显著提示；
- 引用完整性（证据 supersedes、决定与投诉的关系等）在此处检查。
"""

from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

from .models import (
    Complaint,
    ComplaintStatus,
    Dataset,
    DecisionAction,
    EvidenceRecord,
    Issue,
    IssueLevel,
    LiveSession,
    Order,
    RefundState,
    StaffDecision,
)

COMPLAINT_COLUMNS = frozenset(
    {
        "complaint_id",
        "household_ref",
        "account_ref",
        "merchant_ref",
        "course_ref",
        "submitted_at",
        "kind",
        "status",
        "source_ref",
        # 可选列：投诉可直接挂接到直播场次
        "session_ref",
    }
)
ORDER_COLUMNS = frozenset(
    {"order_id", "household_ref", "merchant_ref", "course_ref", "ordered_at", "amount", "refund_state"}
)
EVIDENCE_COLUMNS = frozenset(
    {"evidence_id", "complaint_id", "source_ref", "recorded_at", "fact_kind", "value", "supersedes"}
)
DECISION_COLUMNS = frozenset(
    {"decision_id", "complaint_id", "action", "reviewed_at", "reviewer_ref", "reason_code"}
)
SESSION_COLUMNS = frozenset({"session_ref", "merchant_ref", "course_ref", "started_at", "host_ref"})


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


def parse_iso_datetime(value: str | None) -> datetime | None:
    value = _clean(value)
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"无法解析 ISO 8601 时间: {value!r}")


def _read_csv(path: Path, expected: frozenset[str], issues: list[Issue], required_file: bool) -> list[dict[str, str]]:
    if not path.exists():
        if required_file:
            issues.append(Issue(IssueLevel.ERROR, "missing_file", f"缺少必需资料文件: {path.name}", path.name))
        return []
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames:
            issues.append(Issue(IssueLevel.ERROR, "empty_header", f"{path.name} 缺少字段名行", path.name))
            return []
        missing = expected - set(reader.fieldnames) - {"session_ref"}
        if missing:
            issues.append(
                Issue(
                    IssueLevel.ERROR,
                    "missing_columns",
                    f"{path.name} 缺少字段: {sorted(missing)}",
                    path.name,
                )
            )
        rows: list[dict[str, str]] = []
        for raw in reader:
            rows.append({k: _clean(v) for k, v in raw.items() if k is not None})
        return rows


def load_dataset(data_dir: str | Path) -> tuple[Dataset, list[Issue]]:
    """从目录加载全部资料并做结构与引用校验。"""

    data_dir = Path(data_dir)
    issues: list[Issue] = []

    complaint_rows = _read_csv(data_dir / "complaints.csv", COMPLAINT_COLUMNS, issues, True)
    order_rows = _read_csv(data_dir / "orders.csv", ORDER_COLUMNS, issues, True)
    evidence_rows = _read_csv(data_dir / "evidence.csv", EVIDENCE_COLUMNS, issues, True)
    decision_rows = _read_csv(data_dir / "case_decisions.csv", DECISION_COLUMNS, issues, True)
    session_rows = _read_csv(data_dir / "sessions.csv", SESSION_COLUMNS, issues, False)

    complaints: list[Complaint] = []
    for row in complaint_rows:
        cid = row.get("complaint_id")
        if not cid:
            issues.append(Issue(IssueLevel.ERROR, "missing_id", "投诉记录缺少 complaint_id"))
            continue
        try:
            submitted = parse_iso_datetime(row.get("submitted_at"))
        except ValueError as exc:
            issues.append(Issue(IssueLevel.ERROR, "bad_datetime", str(exc), cid))
            submitted = None
        status = row.get("status") or ""
        if status and status not in {s.value for s in ComplaintStatus}:
            issues.append(
                Issue(IssueLevel.WARNING, "unknown_status", f"未知投诉状态 {status!r}，按 open 处理", cid)
            )
            status = ComplaintStatus.OPEN
        if not row.get("household_ref"):
            issues.append(Issue(IssueLevel.WARNING, "missing_household", "投诉缺少 household_ref", cid))
        complaints.append(
            Complaint(
                complaint_id=cid,
                household_ref=row.get("household_ref") or "",
                account_ref=row.get("account_ref"),
                merchant_ref=row.get("merchant_ref"),
                course_ref=row.get("course_ref"),
                submitted_at=submitted,
                kind=row.get("kind") or "",
                status=status or ComplaintStatus.OPEN,
                source_ref=row.get("source_ref"),
                session_ref=row.get("session_ref"),
            )
        )

    orders: list[Order] = []
    for row in order_rows:
        oid = row.get("order_id")
        if not oid:
            issues.append(Issue(IssueLevel.ERROR, "missing_id", "订单记录缺少 order_id"))
            continue
        try:
            ordered_at = parse_iso_datetime(row.get("ordered_at"))
        except ValueError as exc:
            issues.append(Issue(IssueLevel.ERROR, "bad_datetime", str(exc), oid))
            ordered_at = None
        amount_raw = row.get("amount")
        amount: int | None = None
        if amount_raw is not None:
            try:
                amount = int(amount_raw)
            except ValueError:
                issues.append(Issue(IssueLevel.ERROR, "bad_amount", f"订单金额无法解析: {amount_raw!r}", oid))
        refund = row.get("refund_state") or RefundState.NONE
        if refund not in {s.value for s in RefundState}:
            issues.append(
                Issue(IssueLevel.WARNING, "unknown_refund", f"未知退款状态 {refund!r}，按 none 处理", oid)
            )
            refund = RefundState.NONE
        orders.append(
            Order(
                order_id=oid,
                household_ref=row.get("household_ref") or "",
                merchant_ref=row.get("merchant_ref"),
                course_ref=row.get("course_ref"),
                ordered_at=ordered_at,
                amount=amount,
                refund_state=refund,
            )
        )

    evidence: list[EvidenceRecord] = []
    for row in evidence_rows:
        eid = row.get("evidence_id")
        if not eid:
            issues.append(Issue(IssueLevel.ERROR, "missing_id", "证据记录缺少 evidence_id"))
            continue
        try:
            recorded_at = parse_iso_datetime(row.get("recorded_at"))
        except ValueError as exc:
            issues.append(Issue(IssueLevel.ERROR, "bad_datetime", str(exc), eid))
            recorded_at = None
        evidence.append(
            EvidenceRecord(
                evidence_id=eid,
                complaint_id=row.get("complaint_id") or "",
                source_ref=row.get("source_ref"),
                recorded_at=recorded_at,
                fact_kind=row.get("fact_kind") or "",
                value=row.get("value"),
                supersedes=row.get("supersedes"),
            )
        )

    decisions: list[StaffDecision] = []
    for row in decision_rows:
        did = row.get("decision_id")
        if not did:
            issues.append(Issue(IssueLevel.ERROR, "missing_id", "决定记录缺少 decision_id"))
            continue
        try:
            reviewed_at = parse_iso_datetime(row.get("reviewed_at"))
        except ValueError as exc:
            issues.append(Issue(IssueLevel.ERROR, "bad_datetime", str(exc), did))
            reviewed_at = None
        action = row.get("action") or ""
        if action and action not in {a.value for a in DecisionAction}:
            issues.append(Issue(IssueLevel.ERROR, "unknown_action", f"未知处置动作 {action!r}", did))
        decisions.append(
            StaffDecision(
                decision_id=did,
                complaint_id=row.get("complaint_id") or "",
                action=action,
                reviewed_at=reviewed_at,
                reviewer_ref=row.get("reviewer_ref"),
                reason_code=row.get("reason_code"),
            )
        )

    sessions: list[LiveSession] = []
    for row in session_rows:
        sid = row.get("session_ref")
        if not sid:
            issues.append(Issue(IssueLevel.ERROR, "missing_id", "直播场次记录缺少 session_ref"))
            continue
        try:
            started_at = parse_iso_datetime(row.get("started_at"))
        except ValueError as exc:
            issues.append(Issue(IssueLevel.ERROR, "bad_datetime", str(exc), sid))
            started_at = None
        sessions.append(
            LiveSession(
                session_ref=sid,
                merchant_ref=row.get("merchant_ref"),
                course_ref=row.get("course_ref"),
                started_at=started_at,
                host_ref=row.get("host_ref"),
            )
        )

    _check_references(complaints, orders, evidence, decisions, sessions, issues)
    _check_uniqueness(complaints, orders, evidence, decisions, sessions, issues)

    return Dataset(
        complaints=sorted(complaints, key=lambda c: c.complaint_id),
        orders=sorted(orders, key=lambda o: o.order_id),
        evidence=sorted(evidence, key=lambda e: e.evidence_id),
        decisions=sorted(decisions, key=lambda d: d.decision_id),
        sessions=sorted(sessions, key=lambda s: s.session_ref),
    ), issues


def _check_uniqueness(
    complaints: list[Complaint],
    orders: list[Order],
    evidence: list[EvidenceRecord],
    decisions: list[StaffDecision],
    sessions: list[LiveSession],
    issues: list[Issue],
) -> None:
    for label, rows, key in (
        ("投诉", complaints, "complaint_id"),
        ("订单", orders, "order_id"),
        ("证据", evidence, "evidence_id"),
        ("决定", decisions, "decision_id"),
        ("直播场次", sessions, "session_ref"),
    ):
        seen: set[str] = set()
        for row in rows:
            ident = getattr(row, key)
            if ident in seen:
                issues.append(Issue(IssueLevel.ERROR, "duplicate_id", f"{label}标识重复: {ident}", ident))
            seen.add(ident)


def _check_references(
    complaints: list[Complaint],
    orders: list[Order],
    evidence: list[EvidenceRecord],
    decisions: list[StaffDecision],
    sessions: list[LiveSession],
    issues: list[Issue],
) -> None:
    complaint_ids = {c.complaint_id for c in complaints}
    evidence_ids = {e.evidence_id for e in evidence}
    household_ids = {c.household_ref for c in complaints if c.household_ref}
    session_ids = {s.session_ref for s in sessions}

    for ev in evidence:
        if ev.complaint_id not in complaint_ids:
            issues.append(
                Issue(
                    IssueLevel.ERROR,
                    "dangling_reference",
                    f"证据 {ev.evidence_id} 指向不存在的投诉 {ev.complaint_id}",
                    ev.evidence_id,
                )
            )
        if ev.supersedes and ev.supersedes not in evidence_ids:
            issues.append(
                Issue(
                    IssueLevel.ERROR,
                    "dangling_reference",
                    f"证据 {ev.evidence_id} 补正的旧证据 {ev.supersedes} 不存在",
                    ev.evidence_id,
                )
            )
        if ev.supersedes == ev.evidence_id:
            issues.append(
                Issue(IssueLevel.ERROR, "self_supersede", f"证据 {ev.evidence_id} 不能补正自身", ev.evidence_id)
            )

    for dec in decisions:
        if dec.complaint_id not in complaint_ids:
            issues.append(
                Issue(
                    IssueLevel.ERROR,
                    "dangling_reference",
                    f"决定 {dec.decision_id} 指向不存在的投诉 {dec.complaint_id}",
                    dec.decision_id,
                )
            )

    for order in orders:
        if order.household_ref and order.household_ref not in household_ids:
            issues.append(
                Issue(
                    IssueLevel.WARNING,
                    "unmatched_household",
                    f"订单 {order.order_id} 的家庭 {order.household_ref} 在投诉资料中没有出现",
                    order.order_id,
                )
            )

    for complaint in complaints:
        if complaint.session_ref and complaint.session_ref not in session_ids:
            issues.append(
                Issue(
                    IssueLevel.WARNING,
                    "unmatched_session",
                    f"投诉 {complaint.complaint_id} 指向不存在的直播场次 {complaint.session_ref}",
                    complaint.complaint_id,
                )
            )
