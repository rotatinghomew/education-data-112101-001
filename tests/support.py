"""测试辅助：把行数据写成临时 CSV 资料目录。"""

import sys
import tempfile
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from complaint_analysis.models import AnalysisConfig  # noqa: E402
from complaint_analysis.pipeline import run  # noqa: E402

HEADER = {
    "complaints.csv": (
        "complaint_id,household_ref,account_ref,merchant_ref,course_ref,submitted_at,kind,status,source_ref"
    ),
    "orders.csv": "order_id,household_ref,merchant_ref,course_ref,ordered_at,amount,refund_state",
    "evidence.csv": "evidence_id,complaint_id,source_ref,recorded_at,fact_kind,value,supersedes",
    "case_decisions.csv": "decision_id,complaint_id,action,reviewed_at,reviewer_ref,reason_code",
    "sessions.csv": "session_ref,merchant_ref,course_ref,started_at,host_ref",
}


def write_dataset(tmpdir: str | Path, rows: dict[str, list[str]]) -> Path:
    base = Path(tmpdir)
    for filename, body_rows in rows.items():
        content = HEADER[filename] + "\n"
        if body_rows:
            content += "\n".join(body_rows) + "\n"
        (base / filename).write_text(textwrap.dedent(content), encoding="utf-8")
    return base


def run_pipeline(rows: dict[str, list[str]], config: AnalysisConfig | None = None):
    tmp = tempfile.TemporaryDirectory()
    base = write_dataset(tmp.name, rows)
    report, dataset = run(base, config or AnalysisConfig())
    return report, dataset, tmp


def by_id(items, attr):
    return {getattr(item, attr): item for item in items}
