"""
generate_embeddings.py

One-time (re-runnable) job: builds a text blurb for each provider,
embeds it locally with sentence-transformers, and writes the vector
into the `embedding` column on `providers` via pgvector.
"""
from __future__ import annotations
from sentence_transformers import SentenceTransformer
from sqlalchemy import select, text
from db import SessionLocal, engine
from models import Provider

MODEL_NAME = "all-MiniLM-L6-v2"
BATCH_SIZE = 100


def ensure_embedding_column():
    with engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector;"))
        conn.execute(text(
            "ALTER TABLE providers ADD COLUMN IF NOT EXISTS embedding vector(384);"
        ))
        conn.execute(text(
            "CREATE INDEX IF NOT EXISTS ix_providers_embedding "
            "ON providers USING ivfflat (embedding vector_cosine_ops);"
        ))
        conn.commit()


def build_text_blurb(p: Provider) -> str:
    name = f"{p.first_name or ''} {p.last_name or ''}".strip() or p.organization_name or "Unknown provider"
    specialty = p.taxonomy_desc or "General practice"
    location = ""
    if p.locations:
        loc = p.locations[0]
        location = f" located in {loc.city}, {loc.state} {loc.zip_code}" if loc.city else ""
    return f"{name}, specialty: {specialty}{location}."


def generate_and_store():
    ensure_embedding_column()
    model = SentenceTransformer(MODEL_NAME)

    with SessionLocal() as s:
        providers = s.scalars(select(Provider)).all()
        print(f"Embedding {len(providers)} providers...")

        for i in range(0, len(providers), BATCH_SIZE):
            batch = providers[i:i + BATCH_SIZE]
            texts = [build_text_blurb(p) for p in batch]
            embeddings = model.encode(texts, show_progress_bar=False)

            for p, emb in zip(batch, embeddings):
                vec_str = "[" + ",".join(str(float(x)) for x in emb) + "]"
                s.execute(
                    text("UPDATE providers SET embedding = :emb WHERE id = :id"),
                    {"emb": vec_str, "id": p.id},
                )
            s.commit()
            print(f"  embedded {min(i + BATCH_SIZE, len(providers))}/{len(providers)}")

    print("Done.")


if __name__ == "__main__":
    generate_and_store()
