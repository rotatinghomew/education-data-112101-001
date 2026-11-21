"""投诉与订单的匹配关系（线索层与案件层共用，保证口径一致）。"""

from __future__ import annotations

from datetime import timedelta

from .models import AnalysisConfig, Complaint, Dataset, Order


def match_order(
    complaint: Complaint,
    dataset: Dataset,
    config: AnalysisConfig,
) -> Order | None:
    """按稳定标识（家庭、经营主体、课程）加时间邻近匹配订单。

    匹配要求 household_ref 完全一致；经营主体与课程在双方都提供时必须一致；
    时间差在 same_topic_days 窗口内，取时间最接近的订单。
    """

    window = timedelta(days=config.same_topic_days)
    candidates: list[Order] = []
    for order in dataset.orders:
        if order.household_ref != complaint.household_ref:
            continue
        if order.merchant_ref and complaint.merchant_ref and order.merchant_ref != complaint.merchant_ref:
            continue
        if order.course_ref and complaint.course_ref and order.course_ref != complaint.course_ref:
            continue
        if order.ordered_at is None or complaint.submitted_at is None:
            candidates.append(order)
            continue
        if abs(order.ordered_at - complaint.submitted_at) <= window:
            candidates.append(order)
    if not candidates:
        return None
    candidates.sort(
        key=lambda o: (
            abs(o.ordered_at - complaint.submitted_at)
            if o.ordered_at is not None and complaint.submitted_at is not None
            else timedelta.max,
            o.order_id,
        )
    )
    return candidates[0]


def orders_by_complaint(dataset: Dataset, config: AnalysisConfig) -> dict[str, Order]:
    return {
        complaint.complaint_id: matched
        for complaint in dataset.complaints
        if (matched := match_order(complaint, dataset, config)) is not None
    }
