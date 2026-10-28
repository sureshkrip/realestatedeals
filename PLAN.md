# Jacksonville Real Estate & Foreclosure Finder — Lean Implementation Plan

## Goal
Replace the over-engineered 5-container microservice architecture with a **single, fast, maintainable Python application** (`FastAPI + SQLModel`) that connects directly to your **existing VPS PostgreSQL database** via `DATABASE_URL` and deploys as **one container** on Coolify (`http://realestatefinder.apps.predaccure.com/`).

---

## Architecture Overview

- **1 Container to Deploy**: Only one `Dockerfile` for Coolify to build and run—no separate Next.js build server, Redis broker, or Celery worker containers to maintain.
- **Uses Your Existing VPS Postgres**: Connects directly using `DATABASE_URL=postgresql+psycopg://...`. Tables are automatically created on startup via `SQLModel.metadata.create_all(engine)`.
- **Zero-Key Interactive Maps**: Uses **Leaflet.js + OpenStreetMap** (free, no Mapbox billing or token required).
- **Built to Grow in Phases**: New features (Neighborhood Heatmaps, Kumon Commercial Space Scout) simply add a new SQLModel table and a tab in the UI.

---

## Project Structure

```text
realestatedeals/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, startup DB init, page & API routes
│   ├── config.py            # Pydantic Settings (reads DATABASE_URL from .env)
│   ├── database.py          # SQLModel engine & session management
│   ├── models.py            # SQLModel tables: ForeclosureListing, ZipTrend, CommercialListing
│   ├── services/
│   │   ├── ingester.py      # Duval foreclosure/tax-deed + Property Appraiser sync & CSV import
│   │   └── seed.py          # Realistic Jacksonville seed data & Zillow ZIP baseline
│   └── templates/
│       └── index.html       # Clean Dashboard UI (Tailwind CSS + Alpine.js + Leaflet Map)
├── requirements.txt
├── Dockerfile               # Single lightweight Python 3.12 container for Coolify
├── docker-compose.yml       # Single-service Coolify compose (uses external DATABASE_URL)
└── .env.example             # Template for DATABASE_URL and optional APP_PIN
```

---

## Phased Rollout Plan (Build Simple -> Grow Later)

### Phase 1 — Core MVP: Duval Foreclosure & Tax Deed Finder *(Build Now)*
Get a clean, working site live at `realestatefinder.apps.predaccure.com` immediately.
1. **PostgreSQL Data Model (`ForeclosureListing`)**:
   - `id`, `case_number`, `parcel_id`, `listing_type` (`Foreclosure` | `Tax Deed`)
   - `address`, `city`, `zip_code`, `neighborhood`, `lat`, `lng`
   - `auction_date`, `opening_bid`, `judgment_amount`, `assessed_value`, `estimated_market_value`
   - Computed **Estimated Equity ($ and %)** = `estimated_market_value - opening_bid`
   - Property details: `beds`, `baths`, `sqft`, `year_built`, `property_use`
   - Direct external links: Duval Clerk case URL, Duval Property Appraiser parcel card, Google Street View link
   - Personal tracking: `is_starred` (watchlist toggle), `notes`, `status`
2. **Interactive Dashboard + Map UI**:
   - **Split view**: Filterable deal cards & table on the left + **Leaflet Map** with color-coded pins on the right (Green = High Equity >30%, Amber = Moderate, Gray = Low/Unknown).
   - **Instant Filters**: Listing Type (Foreclosure vs. Tax Deed), Max Opening Bid, Min Equity %, ZIP Code / Neighborhood, Auction Date range, Watchlist/Starred only.
   - **Property Detail Drawer/Modal**: Full breakdown of equity math, parcel info, personal notes, and 1-click links to county records.
3. **Data Ingestion (Simple & Reliable)**:
   - **"Sync / Import" Modal**:
     - **1-Click Duval Feed / Scraper Sync** (with built-in Jacksonville starter dataset so the app is immediately populated and testable).
     - **CSV / JSON Upload**: Drop any exported spreadsheet from Duval Clerk / RealForeclose / RealTaxDeed / Property Appraiser and auto-upsert by `case_number` or `parcel_id`.

---

### Phase 2 — Automated Daily Sync & Watchlist Alerts *(Add Next)*
- **In-Process Scheduler (`APScheduler`)**: Runs a daily morning job inside the same FastAPI container (no Redis/Celery required) to fetch newly scheduled auctions and mark canceled/sold ones.
- **Change Badges**: Highlight `"NEW TODAY"` or `"PRICE DROP"` on listings.

---

### Phase 3 — Jacksonville Neighborhood Trends ("Where It's Booming") *(Add Later)*
- **`ZipTrend` Table**: Stores Zillow Home Value Index (ZHVI) YoY appreciation %, median price/sqft, and hotness score per Jacksonville ZIP (`32204` Riverside, `32207` San Marco, `32250` Jax Beach, `32256` Baymeadows, `32246`, `32210`, etc.).
- **Deal Enrichment**: Every foreclosure card automatically shows its ZIP's YoY trend badge (e.g., `🔥 ZIP +6.8% YoY`).
- **Map Layer Toggle**: Toggle neighborhood trend shading on the Leaflet map.

---

### Phase 4 — Kumon Commercial Space Scout *(Add Later)*
- **Second Navigation Tab ("Commercial / Kumon Scout")**:
  - **`CommercialListing` Table**: Address, ZIP, SqFt, Monthly Rent ($/sqft/yr), Retail Center Type (Strip Mall / Storefront), Exterior Signage Allowed, Lease Term, School Proximity Score.
  - **Kumon Readiness Score (0–100)**: Auto-calculated against Kumon franchise rules (`1,000–1,500+ sqft`, ground-floor retail center, high family/school density in growing Jacksonville ZIPs).

---

## Verification & Deployment Plan
1. **Scaffold Phase 1 MVP** in this workspace (`app/`, `Dockerfile`, `docker-compose.yml`, `.env.example`).
2. **Verify locally** that FastAPI boots cleanly, initializes the PostgreSQL tables, loads Jacksonville foreclosure & neighborhood data, and renders the interactive map + filters.
3. **Deploy to Coolify**: Point Coolify to the repo/Dockerfile, set `DATABASE_URL` to your existing VPS Postgres instance, and publish to `http://realestatefinder.apps.predaccure.com/`.
