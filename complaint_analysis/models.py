"""领域数据结构。

所有结构均为 dataclass，字段名与 CSV 列名保持一致，便于追溯回原始资料。
空字符串在加载层统一转为 None，表示"资料未提供"，不代表数值为零或业务状态被否定。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class ComplaintStatus(StrEnum):
    OPEN = "open"
    WITHDRAWN = "withdrawn"


class RefundState(StrEnum):
    NONE = "none"
    PARTIAL = "partial"
    FULL = "full"


class DecisionAction(StrEnum):
    KEEP_SEPARATE = "keep_separate"   # 人工确认：不合并（排除机器关联）
    MERGE_REVIEW = "merge_review"     # 人工确认：合并为同一案件
    NEEDS_DOCUMENTS = "needs_documents"  # 人工标记：待补资料
    OPEN_CASE = "open_case"           # 人工确认：单条转个案
    SPLIT = "split"                   # 人工确认：从已合并案件中拆分


class ClueState(StrEnum):
    CANDIDATE = "candidate"      # 机器建议，尚未经人工研判
    CONFIRMED = "confirmed"      # 人工已确认（至少一条合并决定）
    EXCLUDED = "excluded"        # 人工已排除（keep_separate）
    RETRACTED = "retracted"      # 输入更正后成立条件消失


class CaseState(StrEnum):
    ACTIVE = "active"                        # 仍需处理
    AWAITING_DOCUMENTS = "awaiting_documents"  # 待补资料，暂挂
    RESOLVED_REFUNDED = "resolved_refunded"  # 全额退款，争议已解决
    CLOSED_WITHDRAWN = "closed_withdrawn"    # 投诉撤回，案件关闭


class IssueLevel(StrEnum):
    ERROR = "error"      # 阻断性问题，分析结果不可靠
    WARNING = "warning"  # 可继续分析，但需要资料人员关注


# 业务规则：哪些投诉类型表示同一家庭对既有投诉的补充，而非独立投诉。
SUPPLEMENT_KINDS = frozenset({"补充说明"})


@dataclass(frozen=True)
class Complaint:
    complaint_id: str
    household_ref: str
    account_ref: str | None
    merchant_ref: str | None
    course_ref: str | None
    submitted_at: datetime | None
    kind: str
    status: str
    source_ref: str | None
    session_ref: str | None = None


@dataclass(frozen=True)
class Order:
    order_id: str
    household_ref: str
    merchant_ref: str | None
    course_ref: str | None
    ordered_at: datetime | None
    amount: int | None
    refund_state: str


@dataclass(frozen=True)
class EvidenceRecord:
    evidence_id: str
    complaint_id: str
    source_ref: str | None
    recorded_at: datetime | None
    fact_kind: str
    value: str | None
    supersedes: str | None  # 被本条证据补正/取代的旧证据 id


@dataclass(frozen=True)
class StaffDecision:
    decision_id: str
    complaint_id: str
    action: str
    reviewed_at: datetime | None
    reviewer_ref: str | None
    reason_code: str | None


@dataclass(frozen=True)
class LiveSession:
    session_ref: str
    merchant_ref: str | None
    course_ref: str | None
    started_at: datetime | None
    host_ref: str | None


@dataclass(frozen=True)
class Issue:
    level: str
    code: str
    message: str
    ref: str | None = None


@dataclass(frozen=True)
class ReasonItem:
    """关联线索或案件中的单条可解释依据。"""

    rule: str
    detail: str
    evidence_ids: tuple[str, ...] = ()
    decision_ids: tuple[str, ...] = ()


@dataclass
class Clue:
    """机器生成的关联线索。

    线索是"建议"而非事实：state 记录人工研判结论；工作人员确认或排除的
    判断及其依据（decisions/reasons）在输入更正后仍然保留。
    """

    clue_id: str
    rule: str
    members: tuple[str, ...]
    key: str
    state: str
    reasons: list[ReasonItem] = field(default_factory=list)
    decisions: list[StaffDecision] = field(default_factory=list)
    is_supplement_members: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "clue_id": self.clue_id,
            "rule": self.rule,
            "key": self.key,
            "state": self.state,
            "members": list(self.members),
            "is_supplement_members": list(self.is_supplement_members),
            "reasons": [
                {
                    "rule": r.rule,
                    "detail": r.detail,
                    "evidence_ids": list(r.evidence_ids),
                    "decision_ids": list(r.decision_ids),
                }
                for r in self.reasons
            ],
            "decisions": [
                {
                    "decision_id": d.decision_id,
                    "complaint_id": d.complaint_id,
                    "action": d.action,
                    "reviewed_at": d.reviewed_at.isoformat() if d.reviewed_at else None,
                    "reviewer_ref": d.reviewer_ref,
                    "reason_code": d.reason_code,
                }
                for d in self.decisions
            ],
        }


@dataclass
class CaseRecord:
    """经工作人员决定形成的案件。

    机器提出的关联不会直接生成案件；没有任何人工决定覆盖的投诉只在线索中
    以 candidate 形式存在，不会出现在案件汇总里。
    """

    case_id: str
    state: str
    member_complaints: tuple[str, ...]
    basis: list[ReasonItem] = field(default_factory=list)
    decisions: list[StaffDecision] = field(default_factory=list)
    household_ref: str | None = None
    merchant_ref: str | None = None
    course_refs: tuple[str, ...] = ()
    open_complaint_ids: tuple[str, ...] = ()
    withdrawn_complaint_ids: tuple[str, ...] = ()
    eligible_complaint_ids: tuple[str, ...] = ()
    refund_states: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "case_id": self.case_id,
            "state": self.state,
            "household_ref": self.household_ref,
            "merchant_ref": self.merchant_ref,
            "course_refs": list(self.course_refs),
            "member_complaints": list(self.member_complaints),
            "eligible_complaint_ids": list(self.eligible_complaint_ids),
            "open_complaint_ids": list(self.open_complaint_ids),
            "withdrawn_complaint_ids": list(self.withdrawn_complaint_ids),
            "refund_states": list(self.refund_states),
            "basis": [
                {
                    "rule": r.rule,
                    "detail": r.detail,
                    "evidence_ids": list(r.evidence_ids),
                    "decision_ids": list(r.decision_ids),
                }
                for r in self.basis
            ],
            "decisions": [
                {
                    "decision_id": d.decision_id,
                    "complaint_id": d.complaint_id,
                    "action": d.action,
                    "reviewed_at": d.reviewed_at.isoformat() if d.reviewed_at else None,
                    "reviewer_ref": d.reviewer_ref,
                    "reason_code": d.reason_code,
                }
                for d in self.decisions
            ],
        }


@dataclass(frozen=True)
class MetricCell:
    """汇总结果中的一个单元格，携带完整血缘，可追溯到投诉与处置决定。"""

    dimension: str
    key: str
    submitted_total: int
    eligible_count: int
    withdrawn_count: int
    supplement_count: int
    refund_full_count: int
    active_case_count: int
    awaiting_documents_count: int
    closed_case_count: int
    complaint_ids: tuple[str, ...]
    eligible_complaint_ids: tuple[str, ...]
    withdrawn_complaint_ids: tuple[str, ...]
    supplement_complaint_ids: tuple[str, ...]
    case_ids: tuple[str, ...]
    decision_ids: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "dimension": self.dimension,
            "key": self.key,
            "submitted_total": self.submitted_total,
            "eligible_count": self.eligible_count,
            "withdrawn_count": self.withdrawn_count,
            "supplement_count": self.supplement_count,
            "refund_full_count": self.refund_full_count,
            "active_case_count": self.active_case_count,
            "awaiting_documents_count": self.awaiting_documents_count,
            "closed_case_count": self.closed_case_count,
            "complaint_ids": list(self.complaint_ids),
            "eligible_complaint_ids": list(self.eligible_complaint_ids),
            "withdrawn_complaint_ids": list(self.withdrawn_complaint_ids),
            "supplement_complaint_ids": list(self.supplement_complaint_ids),
            "case_ids": list(self.case_ids),
            "decision_ids": list(self.decision_ids),
        }


@dataclass
class Dataset:
    complaints: list[Complaint]
    orders: list[Order]
    evidence: list[EvidenceRecord]
    decisions: list[StaffDecision]
    sessions: list[LiveSession] = field(default_factory=list)


@dataclass(frozen=True)
class AnalysisConfig:
    """研判参数。默认值对应团队当前的人工研判口径。"""

    same_topic_days: int = 7        # 同一家庭+经营主体+课程的归并时间窗
    cross_course_days: int = 30     # 同一家庭跨课程/主体关联的时间窗
    session_minutes: int = 240      # 订单时间与直播场次的匹配容差（分钟）


@dataclass
class AnalysisReport:
    """一次完整分析的输出，所有结果均可从输入资料重算得到。"""

    generated_at: datetime
    config: AnalysisConfig
    issues: list[Issue]
    clues: list[Clue]
    cases: list[CaseRecord]
    metric_cells: list[MetricCell]
    # 血缘索引：投诉/案件 -> 关联对象，供追溯命令直接查询
    complaint_clue_index: dict[str, list[str]] = field(default_factory=dict)
    complaint_case_index: dict[str, str | None] = field(default_factory=dict)
    # 生效后的当事人归属（含更正），complaint_id -> household_ref
    effective_household: dict[str, str] = field(default_factory=dict)
    # 已被补正而失效的证据 id -> 最新替代证据 id
    superseded_evidence: dict[str, str] = field(default_factory=dict)
    input_fingerprint: str = ""
