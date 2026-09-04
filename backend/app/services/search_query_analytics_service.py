"""Aggregates search-query statistics into period totals plus a small set
of app-calculated figures.

Only additive fields (people_searched, people_saw, ordered_units_by_query,
ordered_sum_by_query_rub) are summed. order_rate_calculated_pct is this
app's own ratio over those sums — explicitly NOT an attempt to reproduce
Ozon's own conv_search_to_order_pct_ozon: a real sample showed the two
differ by roughly two orders of magnitude (Ozon's per-row conversion is
clearly computed over something other than the raw "people searched"
count), so guessing at that formula would be worse than being upfront
about using a different, documented one. See app.schemas.search_query.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.search_query_statistic import SearchQueryStatistic
from app.schemas.search_query import SearchQueryAnalyticsOut, TopQueryItem

_TOP_N = 10


def compute_search_query_analytics(
    db: Session,
    *,
    store_id: str,
    product_id: str | None = None,
    period_start=None,
    period_end=None,
) -> SearchQueryAnalyticsOut:
    stmt = select(SearchQueryStatistic).where(SearchQueryStatistic.store_id == store_id)
    if product_id:
        stmt = stmt.where(SearchQueryStatistic.product_id == product_id)
    if period_start:
        stmt = stmt.where(SearchQueryStatistic.period_end >= period_start)
    if period_end:
        stmt = stmt.where(SearchQueryStatistic.period_start <= period_end)

    rows = db.scalars(stmt).all()
    if not rows:
        return SearchQueryAnalyticsOut(has_data=False)

    def total(attr: str) -> int:
        return sum(getattr(r, attr) or 0 for r in rows)

    total_people_searched = total("people_searched")
    total_people_saw = total("people_saw")
    total_ordered_units = total("ordered_units_by_query")
    total_ordered_sum_rub = sum(float(r.ordered_sum_by_query_rub or 0) for r in rows)

    positions = [float(r.position_ozon) for r in rows if r.position_ozon is not None]

    def top(key, reverse=True) -> list[TopQueryItem]:
        ranked = sorted(rows, key=lambda r: (getattr(r, key) or 0), reverse=reverse)
        return [
            TopQueryItem(
                query_text=r.query_text,
                people_searched=r.people_searched,
                people_saw=r.people_saw,
                position_ozon=float(r.position_ozon) if r.position_ozon is not None else None,
                ordered_units_by_query=r.ordered_units_by_query,
                ordered_sum_by_query_rub=float(r.ordered_sum_by_query_rub) if r.ordered_sum_by_query_rub is not None else None,
            )
            for r in ranked[:_TOP_N]
        ]

    return SearchQueryAnalyticsOut(
        has_data=True,
        period_start=min(r.period_start for r in rows),
        period_end=max(r.period_end for r in rows),
        distinct_queries=len({r.query_text for r in rows}),
        total_people_searched=total_people_searched,
        total_people_saw=total_people_saw,
        total_ordered_units=total_ordered_units,
        total_ordered_sum_rub=round(total_ordered_sum_rub, 2),
        order_rate_calculated_pct=round(total_ordered_units / total_people_searched * 100, 4) if total_people_searched else None,
        avg_position_calculated=round(sum(positions) / len(positions), 2) if positions else None,
        top_queries_by_searches=top("people_searched"),
        top_queries_by_orders=top("ordered_units_by_query"),
    )
