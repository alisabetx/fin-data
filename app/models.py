from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, ConfigDict


class FundItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    reg_no: str = Field(alias="regNo")
    name: str
    fund_type: int = Field(alias="fundType")

    fund_size: Optional[int] = Field(default=None, alias="fundSize")

    initiation_date: datetime = Field(alias="initiationDate")
    annual_efficiency: Optional[float] = Field(default=None, alias="annualEfficiency")

    net_asset: Optional[int] = Field(default=None, alias="netAsset")

    date: datetime
    manager: Optional[str] = None
    website_address: Optional[List[str]] = Field(default=None, alias="websiteAddress")


class ProcessedFund(BaseModel):
    reg_no: str
    name: str
    fund_type: int

    fund_size: Optional[int] = None
    annual_efficiency: Optional[float] = None
    net_asset: Optional[int] = None

    date: datetime
    manager: Optional[str] = None
    main_website: Optional[str] = None

    @classmethod
    def from_fipiran(cls, item: FundItem) -> "ProcessedFund":
        main_site: Optional[str] = None
        if item.website_address:
            main_site = item.website_address[0]
        return cls(
            reg_no=item.reg_no,
            name=item.name,
            fund_type=item.fund_type,
            fund_size=item.fund_size,
            annual_efficiency=item.annual_efficiency,
            net_asset=item.net_asset,
            date=item.date,
            manager=item.manager,
            main_website=main_site,
        )


class ExternalFundPayload(BaseModel):
    # payload نهایی که به سرویس خارجی POST می‌کنیم
    source: str
    fetched_at: datetime
    items: List[ProcessedFund]


class ApiJobStatus(BaseModel):
    # مدلی برای گزارش وضعیت هر job در endpoint /jobs
    name: str
    url: str
    interval_seconds: int
    last_run: Optional[datetime] = None
    last_success: Optional[datetime] = None
    last_error: Optional[str] = None
    run_count: int = 0
    enabled: bool = True
