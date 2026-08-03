# ingest_cms.py
# -*- coding: utf-8 -*-

from __future__ import annotations
import os, time, argparse
from typing import Dict, Any, Iterable, List, Tuple
from datetime import datetime, timezone

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from db import SessionLocal, engine
from models import Base, Provider, PracticeLocation, SyncState

load_dotenv()

STREAM_NAME = "cms_physician_supplier"      # logical stream name
MAX_RPS = float(os.getenv("MAX_RPS", "5"))
SLEEP = 0 if MAX_RPS <= 0 else 1.0 / MAX_RPS

# Optional row cap (default 5000). Can override with --max-rows on CLI.
DEFAULT_MAX_ROWS = int(os.getenv("MAX_ROWS", "5000"))

# ---------- Helpers ----------
def now_utc() -> datetime:
    return datetime.now(timezone.utc)

def as_aware_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)

# ---------- CMS Data API v1 ----------
DATAAPI_BASE = "https://data.cms.gov/data-api/v1/dataset"
DATASET_ID = os.getenv("CMS_DATASET_ID", "6fea9d79-0129-4e4c-b1b8-23cd86a4f435")
CMS_LIMIT = int(os.getenv("CMS_LIMIT", "500"))

# (Optional) simple filters to keep early loads small (all optional)
FILTER_STATE = os.getenv("CMS_FILTER_STATE")          # e.g. "MA"
FILTER_CITY  = os.getenv("CMS_FILTER_CITY")           # e.g. "Boston"
FILTER_TYPE  = os.getenv("CMS_FILTER_TYPE")           # e.g. "Cardiology" (matches Rndrng_Prvdr_Type)

def fetch_page_cms_v1(offset: int = 0, limit: int = CMS_LIMIT) -> dict:
    """
    CMS Data API v1 uses limit/offset. Some views may return up to 1000 rows
    even if you request less. If we receive >= req_limit rows, assume there is another page.
    """
    req_limit = min(int(limit), 1000)
    url = f"{DATAAPI_BASE}/{DATASET_ID}/data"

    params: Dict[str, Any] = {"limit": req_limit, "offset": int(offset)}
    # Optional thin filters (keeps early loads small)
    if FILTER_STATE:
        params["Rndrng_Prvdr_State_Abrvtn"] = FILTER_STATE
    if FILTER_CITY:
        params["Rndrng_Prvdr_City"] = FILTER_CITY
    if FILTER_TYPE:
        params["Rndrng_Prvdr_Type"] = FILTER_TYPE

    r = requests.get(url, params=params, timeout=60)
    r.raise_for_status()
    rows = r.json()
    next_offset = offset + req_limit if len(rows) >= req_limit else None
    return {"data": rows, "next_offset": next_offset}

# ---------- Map CMS rows to our schema ----------
def map_cms_physician_supplier(records: List[Dict[str, Any]]) -> List[Tuple[Provider, List[PracticeLocation]]]:
    rows: List[Tuple[Provider, List[PracticeLocation]]] = []
    for r in records:
        npi = str(r.get("Rndrng_NPI") or "").strip()
        if not npi:
            continue  # skip rows without NPI

        prov = Provider(
            npi=npi,
            first_name=(r.get("Rndrng_Prvdr_First_Name") or None),
            last_name=(r.get("Rndrng_Prvdr_Last_Org_Name") or None),
            organization_name=None,
            taxonomy_code=None,
            taxonomy_desc=(r.get("Rndrng_Prvdr_Type") or None),
            accepts_new_patients=True,
            phone=None,
            website=None,
            source_updated_at=None,  # dataset has no per-row updated timestamp
        )

        loc = PracticeLocation(
            address1=(r.get("Rndrng_Prvdr_St1") or None),
            address2=(r.get("Rndrng_Prvdr_St2") or None),
            city=(r.get("Rndrng_Prvdr_City") or None),
            state=(r.get("Rndrng_Prvdr_State_Abrvtn") or None),
            zip_code=(str(r.get("Rndrng_Prvdr_Zip5") or "").strip() or None),
            phone=None,
        )

        rows.append((prov, [loc] if any([loc.address1, loc.city, loc.state, loc.zip_code]) else []))
    return rows

# ---------- Upsert + mark ----------
def upsert_provider(session: Session, prov: Provider, seen_at: datetime) -> Provider:
    existing = session.scalar(select(Provider).where(Provider.npi == prov.npi))
    new_dt = as_aware_utc(prov.source_updated_at)

    if existing:
        existing.first_name = prov.first_name
        existing.last_name = prov.last_name
        existing.organization_name = prov.organization_name
        existing.taxonomy_code = prov.taxonomy_code
        existing.taxonomy_desc = prov.taxonomy_desc
        existing.accepts_new_patients = prov.accepts_new_patients
        existing.phone = prov.phone
        existing.website = prov.website
        existing.source_updated_at = new_dt
        existing.last_seen_at = seen_at
        return existing

    prov.source_updated_at = new_dt
    prov.last_seen_at = seen_at
    session.add(prov)
    session.flush()
    return prov

def ensure_locations(session: Session, provider: Provider, locs: List[PracticeLocation], seen_at: datetime) -> None:
    for L in locs:
        exists = session.scalar(
            select(PracticeLocation).where(
                PracticeLocation.provider_id == provider.id,
                PracticeLocation.address1 == L.address1,
                PracticeLocation.city == L.city,
                PracticeLocation.state == L.state,
                PracticeLocation.zip_code == L.zip_code,
            )
        )
        if exists:
            exists.phone = L.phone or exists.phone
            exists.last_seen_at = seen_at
        else:
            L.provider_id = provider.id
            L.last_seen_at = seen_at
            session.add(L)

# ---------- Checkpoint ----------
def load_state(session: Session) -> SyncState:
    st = session.scalar(select(SyncState).where(SyncState.stream == STREAM_NAME))
    if not st:
        st = SyncState(stream=STREAM_NAME, last_updated_at=None, last_page_cursor=None, notes=None)
        session.add(st); session.commit()
    return st

def save_checkpoint(session: Session, st: SyncState, *, last_updated_at: datetime | None = None,
                    cursor: str | None = None, note: str | None = None) -> None:
    if last_updated_at is not None:
        st.last_updated_at = last_updated_at
    if cursor is not None:
        st.last_page_cursor = cursor
    if note is not None:
        st.notes = note
    session.add(st); session.commit()

# ---------- Sweep (delete rows not seen in this full run) ----------
def sweep_stale(session: Session, seen_cutoff: datetime) -> tuple[int, int]:
    removed_locs = session.query(PracticeLocation).where(
        (PracticeLocation.last_seen_at.is_(None)) | (PracticeLocation.last_seen_at < seen_cutoff)
    ).delete(synchronize_session=False)

    removed_prov = session.query(Provider).where(
        (Provider.last_seen_at.is_(None)) | (Provider.last_seen_at < seen_cutoff)
    ).delete(synchronize_session=False)

    return removed_prov, removed_locs

# ---------- Modes ----------
def sync_incremental(max_rows: int):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as s:
        st = load_state(s)
        offset = int(st.last_page_cursor) if (st.last_page_cursor or "").isdigit() else 0
        limit = CMS_LIMIT
        total_rows = 0
        seen_npIs: set[str] = set()
        print(f"[sync:incremental] start offset={offset} limit={limit} (max_rows={max_rows})")

        while True:
            page = fetch_page_cms_v1(offset=offset, limit=limit)
            data = page.get("data", [])
            # progress logging
            for r in data:
                n = r.get("Rndrng_NPI")
                if n: seen_npIs.add(str(n))
            print(f"[debug] batch_size={len(data)} | cumulative_unique_providers={len(seen_npIs)}")

            rows = map_cms_physician_supplier(data)
            if not rows:
                save_checkpoint(s, st, cursor=None, note="complete")
                print("[sync:incremental] done; no more rows")
                break

            seen_at = now_utc()
            for prov, locs in rows:
                dbp = upsert_provider(s, prov, seen_at)
                ensure_locations(s, dbp, locs, seen_at)
            s.commit()

            total_rows += len(rows)
            next_offset = page.get("next_offset")
            save_checkpoint(s, st, cursor=str(next_offset) if next_offset is not None else None,
                            note=f"+{len(rows)} rows (total {total_rows})")
            print(f"[sync:incremental] offset={offset} rows={len(rows)} next={next_offset}")

            # ---------- stop early if we hit the cap ----------
            if total_rows >= max_rows:
                print(f"[sync:incremental] stopping early at {total_rows} rows (cap={max_rows})")
                break

            if next_offset is None:
                break
            offset = int(next_offset)
            if SLEEP: time.sleep(SLEEP)

        print(f"[sync:incremental] total_rows={total_rows}")

def sync_full(max_rows: int):
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as s:
        st = load_state(s)
        run_started = now_utc()          # anything not touched in this run will be swept
        offset, limit = 0, CMS_LIMIT
        total_rows = 0
        seen_npIs: set[str] = set()
        print(f"[sync:full] start limit={limit} (max_rows={max_rows})")

        while True:
            page = fetch_page_cms_v1(offset=offset, limit=limit)
            data = page.get("data", [])
            # progress logging
            for r in data:
                n = r.get("Rndrng_NPI")
                if n: seen_npIs.add(str(n))
            print(f"[debug] batch_size={len(data)} | cumulative_unique_providers={len(seen_npIs)}")

            rows = map_cms_physician_supplier(data)
            if not rows:
                break

            for prov, locs in rows:
                dbp = upsert_provider(s, prov, run_started)
                ensure_locations(s, dbp, locs, run_started)
            s.commit()

            total_rows += len(rows)
            next_offset = page.get("next_offset")
            save_checkpoint(s, st, cursor=str(next_offset) if next_offset is not None else None,
                            note=f"full progress +{len(rows)} (total {total_rows})")
            print(f"[sync:full] offset={offset} rows={len(rows)} next={next_offset}")

            # ---------- stop early if we hit the cap ----------
            if total_rows >= max_rows:
                print(f"[sync:full] stopping early at {total_rows} rows (cap={max_rows})")
                break

            if next_offset is None:
                break
            offset = int(next_offset)
            if SLEEP: time.sleep(SLEEP)

        # sweep anything not seen this run
        removed_prov, removed_locs = sweep_stale(s, run_started)
        s.commit()
        save_checkpoint(s, st, cursor=None, note=f"full done; removed prov={removed_prov}, locs={removed_locs}")
        print(f"[sync:full] total_rows={total_rows} | removed providers={removed_prov}, locations={removed_locs}")

# ---------- CLI ----------
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["incremental", "full"], default="incremental",
                        help="incremental = resume by offset; full = reload & sweep deletions")
    parser.add_argument("--reset", action="store_true", help="clear paging checkpoint before run")
    parser.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS,
                        help=f"stop after roughly this many ingested rows (default {DEFAULT_MAX_ROWS})")
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)

    if args.reset:
        with SessionLocal() as s:
            st = load_state(s)
            st.last_page_cursor = None
            st.notes = "reset"
            s.add(st); s.commit()
            print("[sync] checkpoint reset")

    if args.mode == "full":
        sync_full(args["max_rows"] if isinstance(args, dict) else args.max_rows)
    else:
        sync_incremental(args["max_rows"] if isinstance(args, dict) else args.max_rows)

if __name__ == "__main__":
    main()
