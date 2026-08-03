# probe_db.py
from sqlalchemy import select
from db import SessionLocal
from models import Provider, PracticeLocation

with SessionLocal() as s:
    provs = s.scalars(select(Provider).limit(10)).all()
    print("Providers in DB:", len(provs))
    for p in provs:
        print(p.npi, p.first_name, p.last_name, p.taxonomy_desc)
        for loc in p.locations:
            print("   ->", loc.address1, loc.city, loc.state, loc.zip_code)
