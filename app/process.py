from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict

from .models import ExternalFundPayload, FundItem, ProcessedFund


def process_fund_compare(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    پردازش خروجی fundcompare:
    - تبدیل هر item به مدل FundItem
    - تبدیل به ProcessedFund (فیلدهای تمیزتر)
    - ساخت payload نهایی برای سرویس خارجی
    """
    raw_items = data.get("items", [])
    items: list[ProcessedFund] = []

    for item_data in raw_items:
        fund_item = FundItem.model_validate(item_data)
        items.append(ProcessedFund.from_fipiran(fund_item))

    payload = ExternalFundPayload(
        source="fipiran_fundcompare",
        fetched_at=datetime.utcnow(),
        items=items,
    )
    return payload.model_dump(mode="json")


ProcessorType = Callable[[Dict[str, Any]], Dict[str, Any]]

PROCESSORS: Dict[str, ProcessorType] = {
    "fund_compare": process_fund_compare,
}
