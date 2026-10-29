import csv
import io
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import quote_plus

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlmodel import Session, col, select

from app.config import settings
from app.database import engine, get_session, init_db
from app.models import ForeclosureListing, ZipTrend, utc_now
from app.security import (
    SESSION_COOKIE_NAME,
    clear_failed_attempts,
    create_session_token,
    get_client_ip,
    is_rate_limited,
    is_request_authenticated,
    record_failed_attempt,
    verify_pin,
)
from app.services.ingester import import_file_bytes, sync_duval_feed, upsert_records
from app.services.seed import seed_database

BASE_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # 5 MB max file upload


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if settings.auto_seed:
        with Session(engine) as session:
            seed_database(session, force_refresh=False)
    yield


app = FastAPI(
    title=settings.app_name,
    version="1.2.0",
    lifespan=lifespan,
    docs_url=None if settings.auth_enabled else "/docs",
    redoc_url=None if settings.auth_enabled else "/redoc",
    openapi_url=None if settings.auth_enabled else "/openapi.json",
)

PUBLIC_PATHS = {"/login", "/api/auth/login", "/api/auth/logout", "/api/health"}


@app.middleware("http")
async def security_and_auth_middleware(request: Request, call_next):
    path = request.url.path

    if settings.auth_enabled and path not in PUBLIC_PATHS:
        if not is_request_authenticated(request):
            if path.startswith("/api/"):
                response = JSONResponse(
                    status_code=401,
                    content={"detail": "Authentication required. Please sign in."},
                )
            else:
                response = RedirectResponse(url="/login", status_code=303)
            _attach_security_headers(request, response)
            return response

    response = await call_next(request)
    _attach_security_headers(request, response)
    return response


def _attach_security_headers(request: Request, response: Response) -> None:
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
    is_https = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").lower() == "https"
    )
    if is_https:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"


class LoginPayload(BaseModel):
    pin: str


class ListingUpdatePayload(BaseModel):
    is_starred: Optional[bool] = None
    notes: Optional[str] = None
    status: Optional[str] = None
    opening_bid: Optional[float] = None
    estimated_market_value: Optional[float] = None


class ManualListingPayload(BaseModel):
    case_number: str
    parcel_id: str = "000000-0000"
    listing_type: str = "Foreclosure"
    county: str = "Duval"
    address: str
    city: str = "Jacksonville"
    zip_code: str = "32204"
    neighborhood: Optional[str] = None
    auction_date: Optional[str] = None
    opening_bid: float
    judgment_amount: Optional[float] = None
    assessed_value: Optional[float] = None
    estimated_market_value: float
    beds: Optional[int] = None
    baths: Optional[float] = None
    sqft: Optional[int] = None
    year_built: Optional[int] = None
    property_use: str = "Single Family"
    plaintiff_or_holder: Optional[str] = None
    notes: str = ""


def _serialize_listing(
    item: ForeclosureListing, zip_map: Dict[str, ZipTrend]
) -> Dict[str, Any]:
    data = item.model_dump()
    data["auction_date"] = item.auction_date.isoformat() if item.auction_date else None
    data["created_at"] = item.created_at.isoformat() if item.created_at else None
    data["updated_at"] = item.updated_at.isoformat() if item.updated_at else None
    full_addr = f"{item.address}, {item.city}, {item.state} {item.zip_code}"
    data["zillow_url"] = f"https://www.zillow.com/homes/{quote_plus(full_addr)}_rb/"

    ztrend = zip_map.get(item.zip_code)
    if ztrend:
        data["zip_yoy_pct"] = ztrend.yoy_appreciation_pct
        data["zip_median_ppsf"] = ztrend.median_price_sqft
        data["zip_hotness"] = ztrend.hotness_score
        data["zip_zhvi"] = ztrend.zhvi_median_value
    else:
        data["zip_yoy_pct"] = None
        data["zip_median_ppsf"] = None
        data["zip_hotness"] = None
        data["zip_zhvi"] = None
    return data


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if not settings.auth_enabled or is_request_authenticated(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"app_name": settings.app_name},
    )


@app.post("/api/auth/login")
def login_submit(payload: LoginPayload, request: Request, response: Response):
    client_ip = get_client_ip(request)
    if is_rate_limited(client_ip):
        raise HTTPException(
            status_code=429,
            detail="Too many failed login attempts. Try again in 15 minutes.",
        )

    if not verify_pin(payload.pin):
        remaining = record_failed_attempt(client_ip)
        raise HTTPException(
            status_code=401,
            detail=f"Invalid passcode ({remaining} attempt{'s' if remaining != 1 else ''} remaining).",
        )

    clear_failed_attempts(client_ip)
    token = create_session_token()
    is_https = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").lower() == "https"
    )
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=is_https,
        path="/",
    )
    return {"status": "authenticated"}


@app.post("/api/auth/logout")
def logout_submit(response: Response):
    response.delete_cookie(key=SESSION_COOKIE_NAME, path="/")
    return {"status": "logged_out"}


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "app_name": settings.app_name,
            "auth_enabled": settings.auth_enabled,
        },
    )


@app.get("/api/health")
def health_check(session: Session = Depends(get_session)):
    count = len(session.exec(select(ForeclosureListing.id)).all())
    return {
        "status": "healthy",
        "app": settings.app_name,
        "listings_count": count,
        "auth_enabled": settings.auth_enabled,
        "database_driver": settings.sqlalchemy_database_url.split("://")[0],
    }


@app.get("/api/listings")
def list_foreclosures(
    county: Optional[str] = None,
    listing_type: Optional[str] = None,
    max_opening_bid: Optional[float] = None,
    min_equity_pct: Optional[float] = None,
    zip_code: Optional[str] = None,
    neighborhood: Optional[str] = None,
    status: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    starred_only: bool = False,
    search: Optional[str] = None,
    sort_by: str = "equity_desc",
    session: Session = Depends(get_session),
):
    stmt = select(ForeclosureListing)

    if county and county.lower() != "all":
        stmt = stmt.where(ForeclosureListing.county == county)
    if listing_type and listing_type.lower() != "all":
        stmt = stmt.where(ForeclosureListing.listing_type == listing_type)
    if max_opening_bid is not None and max_opening_bid > 0:
        stmt = stmt.where(ForeclosureListing.opening_bid <= max_opening_bid)
    if min_equity_pct is not None:
        stmt = stmt.where(ForeclosureListing.estimated_equity_pct >= min_equity_pct)
    if zip_code and zip_code.lower() != "all":
        stmt = stmt.where(ForeclosureListing.zip_code == zip_code)
    if neighborhood and neighborhood.lower() != "all":
        stmt = stmt.where(ForeclosureListing.neighborhood == neighborhood)
    if status and status.lower() != "all":
        stmt = stmt.where(ForeclosureListing.status == status)
    if date_from is not None:
        stmt = stmt.where(ForeclosureListing.auction_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(ForeclosureListing.auction_date <= date_to)
    if starred_only:
        stmt = stmt.where(ForeclosureListing.is_starred == True)  # noqa: E712

    if search and search.strip():
        q = f"%{search.strip()}%"
        stmt = stmt.where(
            col(ForeclosureListing.address).ilike(q)
            | col(ForeclosureListing.case_number).ilike(q)
            | col(ForeclosureListing.parcel_id).ilike(q)
            | col(ForeclosureListing.neighborhood).ilike(q)
            | col(ForeclosureListing.city).ilike(q)
            | col(ForeclosureListing.zip_code).ilike(q)
            | col(ForeclosureListing.notes).ilike(q)
        )

    if sort_by == "equity_desc":
        stmt = stmt.order_by(col(ForeclosureListing.estimated_equity_pct).desc())
    elif sort_by == "equity_dollars_desc":
        stmt = stmt.order_by(col(ForeclosureListing.estimated_equity_dollars).desc())
    elif sort_by == "bid_asc":
        stmt = stmt.order_by(col(ForeclosureListing.opening_bid).asc())
    elif sort_by == "date_asc":
        stmt = stmt.order_by(col(ForeclosureListing.auction_date).asc())
    else:
        stmt = stmt.order_by(col(ForeclosureListing.estimated_equity_pct).desc())

    items = session.exec(stmt).all()
    zips = session.exec(select(ZipTrend)).all()
    zip_map = {z.zip_code: z for z in zips}

    serialized = [_serialize_listing(i, zip_map) for i in items]
    return {"items": serialized, "count": len(serialized)}


@app.get("/api/stats")
def get_dashboard_stats(
    county: Optional[str] = None,
    session: Session = Depends(get_session),
):
    all_items = session.exec(select(ForeclosureListing)).all()
    duval_count = sum(1 for i in all_items if (i.county or "Duval") == "Duval")
    st_johns_count = sum(1 for i in all_items if i.county == "St. Johns")

    if county and county.lower() != "all":
        items = [i for i in all_items if (i.county or "Duval") == county]
        zips = session.exec(
            select(ZipTrend).where(ZipTrend.county == county).order_by(ZipTrend.zip_code)
        ).all()
    else:
        items = all_items
        zips = session.exec(select(ZipTrend).order_by(ZipTrend.zip_code)).all()

    total = len(items)
    high_equity = sum(1 for i in items if i.estimated_equity_pct >= 30.0)
    starred = sum(1 for i in items if i.is_starred)
    foreclosures = sum(1 for i in items if i.listing_type == "Foreclosure")
    tax_deeds = sum(1 for i in items if i.listing_type == "Tax Deed")
    avg_equity_pct = (
        round(sum(i.estimated_equity_pct for i in items) / total, 1) if total else 0.0
    )
    total_equity_dollars = round(
        sum(max(0.0, i.estimated_equity_dollars) for i in items), 0
    )

    unique_zips = sorted({i.zip_code for i in items if i.zip_code})
    unique_neighborhoods = sorted({i.neighborhood for i in items if i.neighborhood})

    return {
        "total_listings": total,
        "all_counties_total": len(all_items),
        "duval_count": duval_count,
        "st_johns_count": st_johns_count,
        "high_equity_count": high_equity,
        "starred_count": starred,
        "foreclosure_count": foreclosures,
        "tax_deed_count": tax_deeds,
        "avg_equity_pct": avg_equity_pct,
        "total_equity_dollars": total_equity_dollars,
        "zip_codes": unique_zips,
        "neighborhoods": unique_neighborhoods,
        "zip_trends": [z.model_dump() for z in zips],
    }


@app.get("/api/zips")
def list_zip_trends(
    county: Optional[str] = None,
    session: Session = Depends(get_session),
):
    stmt = select(ZipTrend).order_by(col(ZipTrend.yoy_appreciation_pct).desc())
    if county and county.lower() != "all":
        stmt = stmt.where(ZipTrend.county == county)
    zips = session.exec(stmt).all()
    return {"items": [z.model_dump() for z in zips]}


@app.post("/api/listings")
def create_manual_listing(
    payload: ManualListingPayload, session: Session = Depends(get_session)
):
    result = upsert_records(session, [payload.model_dump()])
    return {"status": "ok", **result}


@app.patch("/api/listings/{listing_id}")
def update_listing(
    listing_id: int,
    payload: ListingUpdatePayload,
    session: Session = Depends(get_session),
):
    listing = session.get(ForeclosureListing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    if payload.is_starred is not None:
        listing.is_starred = payload.is_starred
    if payload.notes is not None:
        listing.notes = payload.notes
    if payload.status is not None:
        listing.status = payload.status
    if payload.opening_bid is not None:
        listing.opening_bid = payload.opening_bid
    if payload.estimated_market_value is not None:
        listing.estimated_market_value = payload.estimated_market_value

    listing.updated_at = utc_now()
    listing.recalculate_metrics_and_links()
    session.add(listing)
    session.commit()
    session.refresh(listing)

    zips = session.exec(select(ZipTrend)).all()
    zip_map = {z.zip_code: z for z in zips}
    return _serialize_listing(listing, zip_map)


@app.delete("/api/listings/{listing_id}")
def delete_listing(listing_id: int, session: Session = Depends(get_session)):
    listing = session.get(ForeclosureListing, listing_id)
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    session.delete(listing)
    session.commit()
    return {"status": "deleted", "id": listing_id}


@app.post("/api/sync")
def trigger_duval_sync(session: Session = Depends(get_session)):
    result = sync_duval_feed(session)
    return {"status": "ok", **result}


@app.post("/api/import")
async def import_csv_or_json(
    request: Request, session: Session = Depends(get_session)
):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Upload exceeds 5 MB limit")

    content_type = request.headers.get("content-type", "")
    filename = request.headers.get("x-filename", "upload.csv")
    content = b""

    if "application/json" in content_type:
        body_json = await request.json()
        if isinstance(body_json, dict) and "file_content" in body_json:
            filename = body_json.get("filename", filename)
            content = str(body_json["file_content"]).encode("utf-8")
        else:
            import json as _json
            filename = "upload.json"
            content = _json.dumps(body_json).encode("utf-8")
    elif "multipart/form-data" in content_type:
        try:
            form = await request.form()
            upload = form.get("file")
            if upload is not None and hasattr(upload, "read"):
                filename = getattr(upload, "filename", None) or filename
                content = await upload.read()
        except Exception:
            raw = await request.body()
            parts = raw.split(b"\r\n\r\n", 1)
            if len(parts) == 2:
                content = parts[1].rsplit(b"\r\n--", 1)[0]
    else:
        content = await request.body()

    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Upload exceeds 5 MB limit")
    if not content:
        raise HTTPException(status_code=400, detail="Uploaded file is empty")
    try:
        stats = import_file_bytes(session, filename, content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Failed to parse file: {exc}") from exc
    return {"status": "ok", "filename": filename, **stats}


@app.get("/api/export.csv")
def export_listings_csv(session: Session = Depends(get_session)):
    items = session.exec(
        select(ForeclosureListing).order_by(col(ForeclosureListing.estimated_equity_pct).desc())
    ).all()
    buffer = io.StringIO()
    fields = [
        "county",
        "case_number",
        "parcel_id",
        "listing_type",
        "address",
        "city",
        "state",
        "zip_code",
        "neighborhood",
        "auction_date",
        "opening_bid",
        "judgment_amount",
        "assessed_value",
        "estimated_market_value",
        "estimated_equity_dollars",
        "estimated_equity_pct",
        "beds",
        "baths",
        "sqft",
        "year_built",
        "property_use",
        "plaintiff_or_holder",
        "status",
        "is_starred",
        "notes",
        "lat",
        "lng",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for item in items:
        row = {k: getattr(item, k) for k in fields}
        if item.auction_date:
            row["auction_date"] = item.auction_date.isoformat()
        writer.writerow(row)

    buffer.seek(0)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=nefl_foreclosures_export.csv"},
    )
