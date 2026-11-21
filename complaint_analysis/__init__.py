"""直播教育消费投诉的关联研判与处置进度分析能力。

模块划分：
- models:    领域数据结构与常量
- loading:   读取并校验 CSV 资料（投诉、订单、证据、人工决定，可选直播场次）
- evidence:  证据补正链与当事人更正的生效计算
- linkage:   基于稳定标识生成可解释的关联线索（机器建议，不等于事实）
- cases:     依据工作人员决定生成案件并计算处置状态
- metrics:   按时间、课程、经营主体汇总，且每个结果可追溯
- lineage:   单条投诉、案件、统计结果的血缘追溯
- pipeline:  把以上步骤串成可重复运行的纯函数式流程
- cli:       命令行入口
"""

from .models import (
    AnalysisConfig,
    AnalysisReport,
    CaseRecord,
    Clue,
    Complaint,
    Dataset,
    EvidenceRecord,
    Issue,
    MetricCell,
    Order,
    ReasonItem,
    StaffDecision,
)
from .pipeline import run

__all__ = [
    "AnalysisConfig",
    "AnalysisReport",
    "CaseRecord",
    "Clue",
    "Complaint",
    "Dataset",
    "EvidenceRecord",
    "Issue",
    "MetricCell",
    "Order",
    "ReasonItem",
    "StaffDecision",
    "run",
]
