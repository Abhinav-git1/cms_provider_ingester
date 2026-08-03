"""
geocode_locations.py

One-time (re-runnable, resumable) job: geocodes each practice location's
address into lat/lon using Nominatim (OpenStreetMap), respecting their
1 request/second rate limit. Only geocodes rows where lat/lon is still NULL,
so it's safe to re-run if interrupted.
"""
from __future__ import annotations
import time
from sqlalchemy import select
from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter

from db import SessionLocal
from models import PracticeLocation

geolocator = Nominatim(user_agent="cms_provider_ingester_portfolio_project")
geocode = RateLimiter(geolocator.geocode, min_delay_seconds=1.1)


def build_address(loc: PracticeLocation) -> str:
    parts = [loc.address1, loc.city, loc.state, loc.zip_code]
    return ", ".join(p for p in parts if p)


def geocode_all():
    with SessionLocal() as s:
        locations = s.scalars(
            select(PracticeLocation).where(PracticeLocation.lat.is_(None))
        ).all()

        total = len(locations)
        print(f"Geocoding {total} locations without coordinates...")

        success = 0
        failed = 0

        for i, loc in enumerate(locations, start=1):
            address = build_address(loc)
            if not address:
                failed += 1
                continue

            try:
                result = geocode(address)
                if result:
                    loc.lat = result.latitude
                    loc.lon = result.longitude
                    success += 1
                else:
                    failed += 1
            except Exception as e:
                print(f"  [warn] failed to geocode '{address}': {e}")
                failed += 1

            if i % 50 == 0:
                s.commit()
                print(f"  progress: {i}/{total} (success={success}, failed={failed})")

        s.commit()
        print(f"Done. success={success}, failed={failed}")


if __name__ == "__main__":
    geocode_all()
