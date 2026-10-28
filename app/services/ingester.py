import csv
import io
import json
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple
from sqlmodel import Session, select
from app.models import ForeclosureListing, ZipTrend, utc_now
from app.services.seed import seed_database


# Default coordinates per Jacksonville ZIP when imported CSV rows lack lat/lng
JAX_ZIP_COORDS: Dict[str, Tuple[float, float, str]] = {
    "32202": (30.3275, -81.6566, "Downtown Jacksonville"),
    "32204": (30.3135, -81.6820, "Riverside / Five Points"),
    "32205": (30.3036, -81.7145, "Avondale / Murray Hill"),
    "32206": (30.3502, -81.6531, "Historic Springfield"),
    "32207": (30.2995, -81.6448, "San Marco / St. Nicholas"),
    "32208": (30.3910, -81.6780, "Northshore / Panama Park"),
    "32209": (30.3580, -81.6980, "Moncrief / Durkeeville"),
    "32210": (30.2588, -81.7376, "Ortega / Cedar Hills"),
    "32211": (30.3358, -81.5861, "Arlington / Old Mill Branch"),
    "32216": (30.2764, -81.5764, "Southpoint / Sans Souci"),
    "32217": (30.2360, -81.6220, "San Jose / Lakewood"),
    "32218": (30.4685, -81.6311, "Oceanway / Northside"),
    "32221": (30.2890, -81.7920, "Normandy / Chaffee"),
    "32223": (30.1583, -81.6331, "Mandarin"),
    "32224": (30.2647, -81.4662, "Hodges / Glen Kernan / Town Center"),
    "32225": (30.3480, -81.4780, "Fort Caroline / Girvin"),
    "32233": (30.3340, -81.4060, "Atlantic Beach / Mayport"),
    "32244": (30.2050, -81.7650, "Argyle Forest / Oakleaf"),
    "32246": (30.3178, -81.4872, "Sandalwood / Intracoastal West"),
    "32250": (30.2840, -81.3961, "Jacksonville Beach"),
    "32256": (30.2075, -81.5512, "Baymeadows / Deerwood"),
    "32257": (30.1940, -81.6130, "Sunbeam / Mandarin North"),
    "32258": (30.1380, -81.5450, "Bartram Park / Julington Creek"),
    "32266": (30.3140, -81.3980, "Neptune Beach"),
    "32277": (30.3690, -81.5880, "Fort Caroline / University Club"),
}


def _clean_currency(val: Any) -> Optional[float]:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        return float(val)
    cleaned = re.sub(r"[^0-9.\-]", "", str(val))
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _clean_int(val: Any) -> Optional[int]:
    num = _clean_currency(val)
    return int(round(num)) if num is not None else None


def _parse_date(val: Any) -> Optional[date]:
    if not val:
        return None
    if isinstance(val, date) and not isinstance(val, datetime):
        return val
    if isinstance(val, datetime):
        return val.date()
    s = str(val).strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y", "%Y/%m/%d", "%m/%d/%y"):
        try:
            return datetime.strptime(s[:10], fmt).date()
        except ValueError:
            continue
    return None


def _pick(row: Dict[str, Any], keys: List[str], default: Any = None) -> Any:
    lower_map = {str(k).strip().lower().replace(" ", "_").replace("#", "num"): v for k, v in row.items()}
    for k in keys:
        if k in lower_map and lower_map[k] not in (None, ""):
            return lower_map[k]
    return default


def normalize_record(raw: Dict[str, Any], index_offset: int = 1) -> Dict[str, Any]:
    """Normalize an arbitrary CSV/JSON row from Duval Clerk, RealForeclose, RealTaxDeed, or PAO."""
    case_number = str(
        _pick(
            raw,
            ["case_number", "case_num", "case", "case_id", "certificate_number", "tax_deed_number"],
            default=f"DUVAL-IMP-{index_offset:04d}",
        )
    ).strip()

    parcel_id = str(
        _pick(
            raw,
            ["parcel_id", "parcel", "re_number", "re_num", "re", "folio", "strap"],
            default="000000-0000",
        )
    ).strip()

    raw_type = str(
        _pick(raw, ["listing_type", "type", "auction_type", "sale_type"], default="Foreclosure")
    ).strip()
    listing_type = "Tax Deed" if "tax" in raw_type.lower() else "Foreclosure"

    address = str(
        _pick(raw, ["address", "property_address", "street_address", "site_address", "location"], default="Unknown Address")
    ).strip()

    city = str(_pick(raw, ["city", "property_city"], default="Jacksonville")).strip()
    state = str(_pick(raw, ["state", "property_state"], default="FL")).strip()
    zip_code = str(_pick(raw, ["zip_code", "zip", "postal_code", "property_zip"], default="32204")).strip()[:5]

    default_lat, default_lng, default_nbhd = JAX_ZIP_COORDS.get(
        zip_code, (30.3322, -81.6557, "Duval County")
    )
    # Apply small deterministic offset from case_number hash so pins in same ZIP don't stack identically
    jitter = ((sum(ord(c) for c in case_number) % 40) - 20) * 0.0004

    lat = _clean_currency(_pick(raw, ["lat", "latitude"])) or round(default_lat + jitter, 6)
    lng = _clean_currency(_pick(raw, ["lng", "lon", "longitude"])) or round(default_lng - jitter, 6)
    neighborhood = str(_pick(raw, ["neighborhood", "subdivision", "area"], default=default_nbhd)).strip()

    opening_bid = _clean_currency(
        _pick(raw, ["opening_bid", "min_bid", "minimum_bid", "starting_bid", "final_judgment", "judgment_amount"])
    ) or 0.0
    judgment_amount = _clean_currency(
        _pick(raw, ["judgment_amount", "final_judgment", "judgment", "cert_amount"])
    ) or opening_bid
    assessed_value = _clean_currency(_pick(raw, ["assessed_value", "just_value", "tax_assessed_value"]))
    estimated_market_value = (
        _clean_currency(_pick(raw, ["estimated_market_value", "market_value", "arv", "zestimate", "est_value"]))
        or (round(assessed_value * 1.18, 2) if assessed_value else round(opening_bid * 1.35, 2))
    )

    return {
        "case_number": case_number,
        "parcel_id": parcel_id,
        "listing_type": listing_type,
        "address": address,
        "city": city,
        "state": state,
        "zip_code": zip_code,
        "neighborhood": neighborhood,
        "lat": lat,
        "lng": lng,
        "auction_date": _parse_date(_pick(raw, ["auction_date", "sale_date", "date"])),
        "opening_bid": opening_bid,
        "judgment_amount": judgment_amount,
        "assessed_value": assessed_value,
        "estimated_market_value": estimated_market_value,
        "beds": _clean_int(_pick(raw, ["beds", "bedrooms", "br"])),
        "baths": _clean_currency(_pick(raw, ["baths", "bathrooms", "ba"])),
        "sqft": _clean_int(_pick(raw, ["sqft", "living_area", "heated_sqft", "square_feet"])),
        "lot_sqft": _clean_int(_pick(raw, ["lot_sqft", "lot_size"])),
        "year_built": _clean_int(_pick(raw, ["year_built", "yr_blt", "built"])),
        "property_use": str(_pick(raw, ["property_use", "property_type", "use_code"], default="Single Family")).strip(),
        "plaintiff_or_holder": _pick(raw, ["plaintiff_or_holder", "plaintiff", "certificate_holder", "lender"]),
        "notes": str(_pick(raw, ["notes", "comments", "description"], default="")).strip(),
        "status": str(_pick(raw, ["status"], default="Upcoming")).strip(),
        "source": str(_pick(raw, ["source"], default="CSV / Manual Import")).strip(),
    }


def upsert_records(session: Session, raw_records: List[Dict[str, Any]]) -> Dict[str, int]:
    """Normalize and upsert a batch of records into ForeclosureListing by case_number or parcel_id."""
    added = 0
    updated = 0
    for idx, raw in enumerate(raw_records, start=1):
        norm = normalize_record(raw, index_offset=idx)
        existing = session.exec(
            select(ForeclosureListing).where(ForeclosureListing.case_number == norm["case_number"])
        ).first()
        if not existing and norm["parcel_id"] and norm["parcel_id"] != "000000-0000":
            existing = session.exec(
                select(ForeclosureListing).where(
                    ForeclosureListing.parcel_id == norm["parcel_id"],
                    ForeclosureListing.listing_type == norm["listing_type"],
                )
            ).first()

        if existing:
            for k, v in norm.items():
                if k in ("notes",) and existing.notes and not v:
                    continue
                if v is not None:
                    setattr(existing, k, v)
            existing.updated_at = utc_now()
            existing.recalculate_metrics_and_links()
            session.add(existing)
            updated += 1
        else:
            listing = ForeclosureListing(**norm)
            listing.recalculate_metrics_and_links()
            session.add(listing)
            added += 1

    session.commit()
    return {"added": added, "updated": updated, "total_processed": len(raw_records)}


def import_file_bytes(session: Session, filename: str, content: bytes) -> Dict[str, int]:
    """Parse uploaded CSV or JSON bytes and upsert into PostgreSQL."""
    text = content.decode("utf-8-sig", errors="replace").strip()
    if filename.lower().endswith(".json") or text.startswith("[") or text.startswith("{"):
        parsed = json.loads(text)
        records = parsed if isinstance(parsed, list) else parsed.get("listings", [parsed])
    else:
        reader = csv.DictReader(io.StringIO(text))
        records = [dict(row) for row in reader if any(row.values())]

    return upsert_records(session, records)


def sync_duval_feed(session: Session) -> Dict[str, Any]:
    """Run the 1-click Duval Foreclosure / Tax Deed & ZIP baseline sync."""
    result = seed_database(session, force_refresh=True)
    total_listings = len(session.exec(select(ForeclosureListing.id)).all())
    total_zips = len(session.exec(select(ZipTrend.id)).all())
    return {
        **result,
        "total_listings": total_listings,
        "total_zips": total_zips,
        "synced_at": utc_now().isoformat(),
    }
