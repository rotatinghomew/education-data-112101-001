"""命令行入口。

子命令：
  validate  资料结构与引用校验
  clues     关联线索报告（机器建议，含人工研判状态与依据）
  cases     案件台账（仅由工作人员决定形成）
  metrics   按时间/课程/经营主体的汇总，每个结果可追溯
  trace     追溯单条投诉、案件或统计单元格的血缘

用法示例：
  python3 cli.py --data ../data clues
  python3 cli.py --data ../data trace --complaint C-101
  python3 cli.py --data ../data metrics --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 支持以隔离模式直接运行脚本（python3 -I cli.py ...）：
# 隔离模式下脚本目录不会自动进入 sys.path，这里显式加入包的父目录。
_PACKAGE_PARENT = Path(__file__).resolve().parent.parent
if str(_PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(_PACKAGE_PARENT))

from complaint_analysis.lineage import trace_case, trace_complaint, trace_metric  # noqa: E402
from complaint_analysis.models import AnalysisConfig  # noqa: E402
from complaint_analysis.pipeline import run  # noqa: E402


def _emit(payload, args) -> None:
    if args.json:
        text = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    else:
        text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=2, default=str)
    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"已写入 {args.out}")
    else:
        print(text)


def _config_from_args(args) -> AnalysisConfig:
    return AnalysisConfig(
        same_topic_days=args.same_topic_days,
        cross_course_days=args.cross_course_days,
        session_minutes=args.session_minutes,
    )


def _print_issues(issues) -> str:
    if not issues:
        return "校验通过：未发现结构或引用问题。"
    lines = [f"发现 {len(issues)} 个问题："]
    for issue in issues:
        lines.append(f"  [{issue.level}] {issue.code} {issue.message}" + (f"（对象: {issue.ref}）" if issue.ref else ""))
    return "\n".join(lines)


def cmd_validate(args) -> None:
    report, _ = run(args.data, _config_from_args(args))
    payload = (
        {"input_fingerprint": report.input_fingerprint, "issues": [i.__dict__ for i in report.issues]}
        if args.json
        else f"输入指纹: {report.input_fingerprint}\n" + _print_issues(report.issues)
    )
    _emit(payload, args)


def cmd_clues(args) -> None:
    report, _ = run(args.data, _config_from_args(args))
    if args.json:
        _emit({"clues": [clue.to_dict() for clue in report.clues]}, args)
        return
    state_name = {
        "candidate": "待研判",
        "confirmed": "已确认",
        "excluded": "已排除",
        "retracted": "已撤回",
    }
    lines = [f"关联线索报告（输入指纹 {report.input_fingerprint}），共 {len(report.clues)} 条：", ""]
    for clue in report.clues:
        lines.append(
            f"{clue.clue_id}  [{state_name.get(clue.state, clue.state)}] 规则={clue.rule} 匹配键={clue.key}"
        )
        lines.append(f"  成员: {', '.join(clue.members)}")
        if clue.is_supplement_members:
            lines.append(f"  其中补充类（不单独成案）: {', '.join(clue.is_supplement_members)}")
        for reason in clue.reasons:
            extra = []
            if reason.evidence_ids:
                extra.append(f"证据 {','.join(reason.evidence_ids)}")
            if reason.decision_ids:
                extra.append(f"决定 {','.join(reason.decision_ids)}")
            suffix = f"（{'；'.join(extra)}）" if extra else ""
            lines.append(f"  依据[{reason.rule}]: {reason.detail}{suffix}")
        if clue.decisions:
            lines.append(
                "  人工决定: "
                + "；".join(
                    f"{d.decision_id}:{d.action}" + (f"({d.reason_code})" if d.reason_code else "")
                    for d in clue.decisions
                )
            )
        lines.append("")
    _emit("\n".join(lines), args)


def cmd_cases(args) -> None:
    report, _ = run(args.data, _config_from_args(args))
    if args.json:
        _emit({"cases": [case.to_dict() for case in report.cases]}, args)
        return
    state_name = {
        "active": "处理中",
        "awaiting_documents": "待补资料",
        "resolved_refunded": "已解决(全额退款)",
        "closed_withdrawn": "已关闭(撤回)",
    }
    lines = [f"案件台账（仅含工作人员决定形成的案件），共 {len(report.cases)} 件：", ""]
    for case in report.cases:
        lines.append(f"{case.case_id}  [{state_name.get(case.state, case.state)}]  当事人={case.household_ref or '未知'}")
        lines.append(f"  成员投诉: {', '.join(case.member_complaints)}")
        lines.append(f"  可计入投诉: {', '.join(case.eligible_complaint_ids) or '无'}")
        if case.withdrawn_complaint_ids:
            lines.append(f"  已撤回: {', '.join(case.withdrawn_complaint_ids)}")
        if case.refund_states:
            lines.append(f"  关联订单退款状态: {', '.join(case.refund_states)}")
        lines.append(
            "  人工决定: "
            + "；".join(
                f"{d.decision_id}:{d.action}" + (f"({d.reason_code})" if d.reason_code else "")
                for d in case.decisions
            )
        )
        for reason in case.basis:
            lines.append(f"  依据[{reason.rule}]: {reason.detail}")
        lines.append("")
    _emit("\n".join(lines), args)


def cmd_metrics(args) -> None:
    report, _ = run(args.data, _config_from_args(args))
    if args.json:
        _emit({"metrics": [cell.to_dict() for cell in report.metric_cells]}, args)
        return
    header = (
        f"{'维度':<10}{'键':<12}{'提交':>5}{'可计入':>7}{'撤回':>5}{'补充':>5}"
        f"{'全额退款':>9}{'处理中案件':>11}{'待补资料':>10}{'已关闭':>8}"
    )
    lines = [f"投诉变化汇总（输入指纹 {report.input_fingerprint}）", header, "-" * len(header)]
    for cell in report.metric_cells:
        lines.append(
            f"{cell.dimension:<10}{cell.key:<12}{cell.submitted_total:>5}{cell.eligible_count:>7}"
            f"{cell.withdrawn_count:>5}{cell.supplement_count:>5}{cell.refund_full_count:>9}"
            f"{cell.active_case_count:>11}{cell.awaiting_documents_count:>10}{cell.closed_case_count:>8}"
        )
    lines += [
        "",
        "血缘提示：每个单元格都可通过 `trace --metric 维度:键` 下钻到投诉、案件与决定 ID。",
        "可计入口径：非撤回且非补充类投诉；案件数只统计工作人员决定形成的案件。",
    ]
    _emit("\n".join(lines), args)


def cmd_trace(args) -> None:
    report, dataset = run(args.data, _config_from_args(args))
    if args.complaint:
        payload = trace_complaint(report, dataset, args.complaint)
    elif args.case:
        payload = trace_case(report, args.case)
    elif args.metric:
        dimension, _, key = args.metric.partition(":")
        if not key:
            payload = {"error": "--metric 格式应为 维度:键，例如 merchant:M-3 或 time:2026-04"}
        else:
            payload = trace_metric(report, dimension, key)
    else:
        payload = {"error": "trace 需要指定 --complaint / --case / --metric 之一"}
    _emit(payload, args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="直播教育消费投诉的关联研判与处置进度分析")
    parser.add_argument("--data", default="data", help="CSV 资料目录（默认 data）")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非中文文本报告")
    parser.add_argument("--out", help="把输出写入文件而非标准输出")
    parser.add_argument("--same-topic-days", type=int, default=7, help="同主题归并时间窗（天）")
    parser.add_argument("--cross-course-days", type=int, default=30, help="跨课程关联时间窗（天）")
    parser.add_argument("--session-minutes", type=int, default=240, help="直播场次匹配容差（分钟）")

    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="校验资料结构与引用完整性")
    sub.add_parser("clues", help="输出关联线索报告")
    sub.add_parser("cases", help="输出案件台账")
    sub.add_parser("metrics", help="输出分维度汇总")
    trace_parser = sub.add_parser("trace", help="追溯投诉/案件/统计结果的血缘")
    target = trace_parser.add_mutually_exclusive_group()
    target.add_argument("--complaint", help="投诉 id，如 C-101")
    target.add_argument("--case", help="案件 id")
    target.add_argument("--metric", help="统计单元格，格式 维度:键，如 merchant:M-3")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {
        "validate": cmd_validate,
        "clues": cmd_clues,
        "cases": cmd_cases,
        "metrics": cmd_metrics,
        "trace": cmd_trace,
    }
    try:
        handlers[args.command](args)
    except FileNotFoundError as exc:
        print(f"资料读取失败: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
