# verify.py
from sqlalchemy import select, func
from db import SessionLocal
from models import Provider, PracticeLocation

with SessionLocal() as s:
    provs = s.scalar(select(func.count()).select_from(Provider))
    locs = s.scalar(select(func.count()).select_from(PracticeLocation))
    print("Total providers:", provs, " | Total locations:", locs)

    rows = s.execute(
        select(Provider, PracticeLocation)
        .join(PracticeLocation, PracticeLocation.provider_id == Provider.id, isouter=True)
        .limit(5)
    ).all()

    for p, l in rows:
        name = (p.organization_name or f"{(p.first_name or '').title()} {(p.last_name or '').title()}").strip()
        where = f"{(l.city if l else '')}, {(l.state if l else '')} {l.zip_code if l else ''}".strip()
        print(f"NPI={p.npi} | {name} | {p.taxonomy_desc} | {p.phone} | {where}")
