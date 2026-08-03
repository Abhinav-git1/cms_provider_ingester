from __future__ import annotations
from typing import List, Optional
from dotenv import load_dotenv
load_dotenv()  # must run before reading DATABASE_URL

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import select, and_, or_, func
from sqlalchemy.orm import joinedload

from db import SessionLocal
from models import Provider, PracticeLocation

app = FastAPI(title="CMS Doctor Finder", version="1.0.0")

# (Optional) allow your local frontends to call the API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten later
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

import os
from fastapi import FastAPI
from db import engine
from sqlalchemy import select, func
from models import Provider

@app.get("/debug/db")
def debug_db():
    return {"cwd": os.getcwd(), "env_DATABASE_URL": os.getenv("DATABASE_URL"), "engine_url": str(engine.url)}

@app.get("/debug/counts")
def debug_counts():
    from db import SessionLocal
    with SessionLocal() as s:
        provs = s.scalar(select(func.count()).select_from(Provider))
    return {"providers": provs}


# ---------- Pydantic response models ----------
class LocationOut(BaseModel):
    address1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    phone: Optional[str] = None

class ProviderOut(BaseModel):
    npi: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    specialty: Optional[str] = None
    locations: List[LocationOut] = []

class ProviderSummaryOut(BaseModel):
    npi: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    specialty: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None

class SpecialtyCountOut(BaseModel):
    specialty: str
    providers: int

class CityCountOut(BaseModel):
    city: str
    state: str
    providers: int

# ---------- DB session helper ----------
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.get("/health")
def health():
    return {"ok": True}

# ---------- Search providers ----------
@app.get("/search/providers", response_model=List[ProviderSummaryOut])
def search_providers(
    q: Optional[str] = Query(None, description="Free text across name & specialty"),
    specialty: Optional[str] = Query(None, description="e.g., Cardiology"),
    city: Optional[str] = None,
    state: Optional[str] = None,
    zip: Optional[str] = None,
    limit: int = 25,
    offset: int = 0,
):
    if limit > 100:
        limit = 100

    with SessionLocal() as s:
        stmt = (
            select(Provider, PracticeLocation)
            .join(PracticeLocation, PracticeLocation.provider_id == Provider.id, isouter=True)
        )

        conds = []
        if q:
            like = f"%{q}%"
            conds.append(or_(
                Provider.first_name.ilike(like),
                Provider.last_name.ilike(like),
                Provider.taxonomy_desc.ilike(like),
            ))
        if specialty:
            conds.append(Provider.taxonomy_desc.ilike(f"%{specialty}%"))
        if city:
            conds.append(PracticeLocation.city.ilike(city))
        if state:
            conds.append(PracticeLocation.state.ilike(state))
        if zip:
            conds.append(PracticeLocation.zip_code.ilike(f"{zip}%"))

        if conds:
            stmt = stmt.where(and_(*conds))

        stmt = stmt.limit(limit).offset(offset)

        rows = s.execute(stmt).all()
        out: List[ProviderSummaryOut] = []
        for p, l in rows:
            out.append(ProviderSummaryOut(
                npi=p.npi,
                first_name=p.first_name,
                last_name=p.last_name,
                specialty=p.taxonomy_desc,
                city=(l.city if l else None),
                state=(l.state if l else None),
                zip_code=(l.zip_code if l else None),
            ))
        return out

# ---------- Get one provider (with all locations) ----------
@app.get("/providers/{npi}", response_model=ProviderOut)
def get_provider(npi: str):
    with SessionLocal() as s:
        stmt = (
            select(Provider)
            .options(joinedload(Provider.locations))
            .where(Provider.npi == npi)
        )
        prov = s.execute(stmt).unique().scalars().first()
        if not prov:
            raise HTTPException(status_code=404, detail="Provider not found")

        return ProviderOut(
            npi=prov.npi,
            first_name=prov.first_name,
            last_name=prov.last_name,
            specialty=prov.taxonomy_desc,
            locations=[
                LocationOut(
                    address1=l.address1,
                    city=l.city,
                    state=l.state,
                    zip_code=l.zip_code,
                    phone=l.phone,
                ) for l in (prov.locations or [])
            ],
        )

# ---------- Specialty leaderboard (optionally filter by state) ----------
@app.get("/specialties", response_model=List[SpecialtyCountOut])
def specialties(state: Optional[str] = None, limit: int = 50):
    if limit > 200:
        limit = 200

    with SessionLocal() as s:
        stmt = (
            select(
                func.coalesce(Provider.taxonomy_desc, "(unknown)").label("specialty"),
                func.count(func.distinct(Provider.id)).label("providers"),
            )
            .select_from(Provider)
            .join(PracticeLocation, PracticeLocation.provider_id == Provider.id, isouter=True)
        )
        if state:
            stmt = stmt.where(PracticeLocation.state.ilike(state))
        stmt = stmt.group_by("specialty").order_by(func.count(func.distinct(Provider.id)).desc()).limit(limit)
        rows = s.execute(stmt).all()
        return [SpecialtyCountOut(specialty=r[0], providers=r[1]) for r in rows]

# ---------- Cities leaderboard (optionally filter by state & specialty) ----------
@app.get("/cities", response_model=List[CityCountOut])
def cities(state: Optional[str] = None, specialty: Optional[str] = None, limit: int = 50):
    if limit > 200:
        limit = 200

    with SessionLocal() as s:
        stmt = (
            select(
                func.coalesce(PracticeLocation.city, "(unknown)").label("city"),
                func.coalesce(PracticeLocation.state, "(--)").label("state"),
                func.count(func.distinct(Provider.id)).label("providers"),
            )
            .select_from(Provider)
            .join(PracticeLocation, PracticeLocation.provider_id == Provider.id)
        )
        if state:
            stmt = stmt.where(PracticeLocation.state.ilike(state))
        if specialty:
            stmt = stmt.where(Provider.taxonomy_desc.ilike(f"%{specialty}%"))

        stmt = stmt.group_by("city", "state").order_by(func.count(func.distinct(Provider.id)).desc()).limit(limit)
        rows = s.execute(stmt).all()
        return [CityCountOut(city=r[0], state=r[1], providers=r[2]) for r in rows]


from generate_answer import answer_question

class ChatRequest(BaseModel):
    question: str

class ChatResponse(BaseModel):
    answer: str

@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    answer = answer_question(req.question)
    return ChatResponse(answer=answer)

