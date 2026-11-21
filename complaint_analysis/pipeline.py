"""分析流水线：加载 -> 证据生效计算 -> 线索 -> 案件 -> 汇总 -> 索引。

整个流程是纯函数式的：同样的输入资料与配置必然得到同样的输出，
输入更正后重新运行即可得到一致更新，不需要人工清理中间状态。
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .cases import build_cases
from .evidence import build_supersede_map, effective_households
from .linkage import build_clues
from .loading import load_dataset
from .metrics import build_metrics
from .models import AnalysisConfig, AnalysisReport, Dataset, Issue, IssueLevel, SUPPLEMENT_KINDS


def _fingerprint(data_dir: Path) -> str:
    """对输入文件内容取指纹，用于核对两次运行的输入是否一致。"""

    digest = hashlib.sha256()
    for path in sorted(Path(data_dir).glob("*.csv")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def run(data_dir: str | Path, config: AnalysisConfig | None = None) -> tuple[AnalysisReport, Dataset]:
    """运行完整分析，返回报告与加载后的数据集（便于追溯命令复用）。"""

    config = config or AnalysisConfig()
    data_dir = Path(data_dir)
    dataset, load_issues = load_dataset(data_dir)

    superseded = build_supersede_map(dataset.evidence)
    effective, _ = effective_households(dataset)
    clues = build_clues(dataset, config)
    cases = build_cases(dataset, clues, config)
    metric_cells = build_metrics(dataset, cases, config)

    complaint_clue_index: dict[str, list[str]] = defaultdict(list)
    for clue in clues:
        for cid in clue.members:
            complaint_clue_index[cid].append(clue.clue_id)

    complaint_case_index: dict[str, str | None] = {}
    for case in cases:
        for cid in case.member_complaints:
            complaint_case_index[cid] = case.case_id
    for complaint in dataset.complaints:
        complaint_case_index.setdefault(complaint.complaint_id, None)

    issues = list(load_issues)
    _cross_check(dataset, clues, cases, issues)

    report = AnalysisReport(
        generated_at=datetime.now(),
        config=config,
        issues=issues,
        clues=clues,
        cases=cases,
        metric_cells=metric_cells,
        complaint_clue_index=dict(complaint_clue_index),
        complaint_case_index=complaint_case_index,
        effective_household=effective,
        superseded_evidence=superseded,
        input_fingerprint=_fingerprint(data_dir),
    )
    return report, dataset


def _cross_check(dataset, clues, cases, issues: list[Issue]) -> None:
    """结果一致性交叉检查，任何异常都作为 error 暴露，而不是静默吞掉。"""

    complaint_ids = {c.complaint_id for c in dataset.complaints}
    for clue in clues:
        unknown = [cid for cid in clue.members if cid not in complaint_ids]
        if unknown:
            issues.append(
                Issue(
                    IssueLevel.ERROR,
                    "clue_dangling_member",
                    f"线索 {clue.clue_id} 包含不存在的投诉 {unknown}",
                    clue.clue_id,
                )
            )
    case_members: dict[str, str] = {}
    for case in cases:
        for cid in case.member_complaints:
            if cid in case_members and case_members[cid] != case.case_id:
                issues.append(
                    Issue(
                        IssueLevel.ERROR,
                        "case_overlap",
                        f"投诉 {cid} 同时进入案件 {case_members[cid]} 与 {case.case_id}",
                        cid,
                    )
                )
            case_members[cid] = case.case_id
    # 补充类投诉不应被当作独立案件主成员。
    supplement_ids = {c.complaint_id for c in dataset.complaints if c.kind in SUPPLEMENT_KINDS}
    for case in cases:
        solo_supplement = set(case.eligible_complaint_ids) & supplement_ids
        if solo_supplement:
            issues.append(
                Issue(
                    IssueLevel.ERROR,
                    "supplement_miscounted",
                    f"案件 {case.case_id} 把补充投诉计为独立投诉: {sorted(solo_supplement)}",
                    case.case_id,
                )
            )
