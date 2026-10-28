from datetime import date, datetime, timezone
from typing import Optional
from urllib.parse import quote_plus
from sqlmodel import Field, SQLModel


ST_JOHNS_ZIPS = {
    "32080",
    "32081",
    "32082",
    "32084",
    "32086",
    "32092",
    "32095",
    "32259",
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ForeclosureListing(SQLModel, table=True):
    __tablename__ = "foreclosure_listings"

    id: Optional[int] = Field(default=None, primary_key=True)
    case_number: str = Field(index=True, unique=True)
    parcel_id: str = Field(index=True)
    listing_type: str = Field(default="Foreclosure", index=True)  # "Foreclosure" | "Tax Deed"
    county: str = Field(default="Duval", index=True)  # "Duval" | "St. Johns"

    # Location
    address: str
    city: str = Field(default="Jacksonville")
    state: str = Field(default="FL")
    zip_code: str = Field(index=True)
    neighborhood: str = Field(default="Duval County", index=True)
    lat: float = Field(default=30.3322)
    lng: float = Field(default=-81.6557)

    # Financials & Auction
    auction_date: Optional[date] = Field(default=None, index=True)
    opening_bid: float = Field(default=0.0)
    judgment_amount: Optional[float] = Field(default=None)
    assessed_value: Optional[float] = Field(default=None)
    estimated_market_value: float = Field(default=0.0)

    # Precomputed / cached equity fields for fast SQL sorting & filtering
    estimated_equity_dollars: float = Field(default=0.0, index=True)
    estimated_equity_pct: float = Field(default=0.0, index=True)

    # Property Characteristics
    beds: Optional[int] = Field(default=None)
    baths: Optional[float] = Field(default=None)
    sqft: Optional[int] = Field(default=None)
    lot_sqft: Optional[int] = Field(default=None)
    year_built: Optional[int] = Field(default=None)
    property_use: str = Field(default="Single Family")
    plaintiff_or_holder: Optional[str] = Field(default=None)

    # Direct External Links
    clerk_case_url: Optional[str] = Field(default=None)
    pa_parcel_url: Optional[str] = Field(default=None)
    street_view_url: Optional[str] = Field(default=None)
    auction_portal_url: Optional[str] = Field(default=None)

    # Personal Tracking
    is_starred: bool = Field(default=False, index=True)
    notes: str = Field(default="")
    status: str = Field(default="Upcoming", index=True)  # Upcoming | Reviewing | Bid Ready | Sold | Canceled
    source: str = Field(default="County Official Feed")

    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    def recalculate_metrics_and_links(self) -> None:
        """Compute estimated equity ($ and %) and populate official Duval or St. Johns links."""
        if self.estimated_market_value and self.estimated_market_value > 0:
            self.estimated_equity_dollars = round(self.estimated_market_value - (self.opening_bid or 0.0), 2)
            self.estimated_equity_pct = round(
                (self.estimated_equity_dollars / self.estimated_market_value) * 100.0, 1
            )
        else:
            self.estimated_equity_dollars = 0.0
            self.estimated_equity_pct = 0.0

        if not self.county or self.zip_code in ST_JOHNS_ZIPS:
            if self.zip_code in ST_JOHNS_ZIPS:
                self.county = "St. Johns"
            elif not self.county:
                self.county = "Duval"

        clean_re = (self.parcel_id or "").replace("-", "").strip()
        is_sjc = self.county.lower().startswith("st")

        if is_sjc:
            self.pa_parcel_url = "https://qpublic.schneidercorp.com/Application.aspx?AppID=960&LayerID=19106&PageTypeID=2&PageID=8434"
            self.clerk_case_url = "https://apps.stjohnsclerk.com/Benchmark/Home.aspx/Search"
            if self.listing_type.lower() == "tax deed":
                self.auction_portal_url = "https://saintjohns.realtaxdeed.com/"
            else:
                self.auction_portal_url = "https://saintjohns.realforeclose.com/"
        else:
            if clean_re and clean_re != "0000000000" and not clean_re.startswith("DUVAL") and not clean_re.startswith("RE"):
                self.pa_parcel_url = f"https://paopropertysearch.coj.net/Basic/Detail.aspx?RE={clean_re}"
            else:
                self.pa_parcel_url = "https://paopropertysearch.coj.net/Basic/Search.aspx"
            self.clerk_case_url = "https://core.duvalclerk.com/"
            if self.listing_type.lower() == "tax deed":
                self.auction_portal_url = "https://duval.realtaxdeed.com/"
            else:
                self.auction_portal_url = "https://duval.realforeclose.com/"

        full_addr = f"{self.address}, {self.city}, {self.state} {self.zip_code}"
        self.street_view_url = (
            f"https://www.google.com/maps/search/?api=1&query={quote_plus(full_addr)}"
        )


class ZipTrend(SQLModel, table=True):
    __tablename__ = "zip_trends"

    id: Optional[int] = Field(default=None, primary_key=True)
    zip_code: str = Field(index=True, unique=True)
    county: str = Field(default="Duval", index=True)
    neighborhood_name: str
    zhvi_median_value: float = Field(default=0.0)
    yoy_appreciation_pct: float = Field(default=0.0)
    median_price_sqft: float = Field(default=0.0)
    median_days_on_market: int = Field(default=30)
    hotness_score: int = Field(default=50)  # 0-100
    lat: float = Field(default=30.3322)
    lng: float = Field(default=-81.6557)
    summary: str = Field(default="")


class CommercialListing(SQLModel, table=True):
    __tablename__ = "commercial_listings"

    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    address: str
    zip_code: str = Field(index=True)
    neighborhood: str
    sqft: int = Field(default=1200)
    monthly_rent: float = Field(default=2500.0)
    rent_per_sqft_yr: float = Field(default=25.0)
    center_type: str = Field(default="Strip Mall")
    exterior_signage: bool = Field(default=True)
    lease_term: str = Field(default="3-5 Years NNN")
    school_proximity_score: int = Field(default=80)
    kumon_readiness_score: int = Field(default=85)
    lat: float = Field(default=30.3322)
    lng: float = Field(default=-81.6557)
    notes: str = Field(default="")
    listing_url: Optional[str] = Field(default=None)
