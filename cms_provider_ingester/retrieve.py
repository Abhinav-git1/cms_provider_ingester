"""
retrieve.py (v2 -- location-aware)

Two-stage retrieval:
  1. If the query mentions a location, geocode it and filter providers
     to those within a radius (using the Haversine formula, computed
     directly in SQL).
  2. Within that filtered set (or the whole table if no location given),
     rank by semantic similarity to the medical/specialty portion of
     the query using pgvector.
"""
from __future__ import annotations
import re
from typing import List, Optional, Tuple
from sentence_transformers import SentenceTransformer
from sqlalchemy import text
from geopy.geocoders import Nominatim

from db import SessionLocal

MODEL_NAME = "all-MiniLM-L6-v2"
_model = SentenceTransformer(MODEL_NAME)
_geolocator = Nominatim(user_agent="cms_provider_ingester_portfolio_project")

DEFAULT_RADIUS_MILES = 25

LOCATION_PATTERN = re.compile(r"\b(?:near|in|around)\s+(.+)$", re.IGNORECASE)


def parse_query(question: str):
    match = LOCATION_PATTERN.search(question)
    if not match:
        return question, None
    location_text = match.group(1).strip()
    specialty_query = question[:match.start()].strip()
    if not specialty_query:
        specialty_query = question
    return specialty_query, location_text


def geocode_location(location_text: str):
    result = _geolocator.geocode(location_text)
    if result:
        return result.latitude, result.longitude
    return None


def embed_query(query: str) -> str:
    vec = _model.encode([query])[0]
    return "[" + ",".join(str(float(x)) for x in vec) + "]"


def retrieve_similar_providers(question: str, top_k: int = 5, radius_miles: float = DEFAULT_RADIUS_MILES) -> List[dict]:
    specialty_query, location_text = parse_query(question)
    query_vec = embed_query(specialty_query)

    lat_lon = None
    if location_text:
        lat_lon = geocode_location(location_text)
        if lat_lon is None:
            print(f"[warn] could not geocode '{location_text}', ignoring location filter")

    with SessionLocal() as s:
        if lat_lon:
            lat, lon = lat_lon
            sql = text("""
                SELECT
                    p.npi, p.first_name, p.last_name, p.organization_name,
                    p.taxonomy_desc, l.city, l.state, l.zip_code,
                    (p.embedding <=> :query_vec) AS distance,
                    (
                        3959 * acos(
                            cos(radians(:qlat)) * cos(radians(l.lat)) *
                            cos(radians(l.lon) - radians(:qlon)) +
                            sin(radians(:qlat)) * sin(radians(l.lat))
                        )
                    ) AS miles_away
                FROM providers p
                JOIN practice_locations l ON l.provider_id = p.id
                WHERE p.embedding IS NOT NULL
                  AND l.lat IS NOT NULL AND l.lon IS NOT NULL
                  AND (
                        3959 * acos(
                            cos(radians(:qlat)) * cos(radians(l.lat)) *
                            cos(radians(l.lon) - radians(:qlon)) +
                            sin(radians(:qlat)) * sin(radians(l.lat))
                        )
                      ) <= :radius
                ORDER BY p.embedding <=> :query_vec
                LIMIT :top_k
            """)
            rows = s.execute(sql, {
                "query_vec": query_vec, "qlat": lat, "qlon": lon,
                "radius": radius_miles, "top_k": top_k,
            }).mappings().all()
        else:
            sql = text("""
                SELECT
                    p.npi, p.first_name, p.last_name, p.organization_name,
                    p.taxonomy_desc, l.city, l.state, l.zip_code,
                    (p.embedding <=> :query_vec) AS distance
                FROM providers p
                LEFT JOIN practice_locations l ON l.provider_id = p.id
                WHERE p.embedding IS NOT NULL
                ORDER BY p.embedding <=> :query_vec
                LIMIT :top_k
            """)
            rows = s.execute(sql, {"query_vec": query_vec, "top_k": top_k}).mappings().all()

        return [dict(r) for r in rows]


if __name__ == "__main__":
    tests = [
        "who can help with chest pain",
        "who can help with chest pain near Bethesda MD",
    ]
    for q in tests:
        print(f"\nQuery: {q}")
        results = retrieve_similar_providers(q, top_k=5)
        if not results:
            print("  No results (location filter may have excluded everyone -- try a larger radius)")
        for r in results:
            name = f"{r['first_name'] or ''} {r['last_name'] or ''}".strip() or r['organization_name']
            extra = f", {r['miles_away']:.1f} mi away" if "miles_away" in r else ""
            print(f"  {name} | {r['taxonomy_desc']} | {r['city']}, {r['state']} | distance={r['distance']:.4f}{extra}")