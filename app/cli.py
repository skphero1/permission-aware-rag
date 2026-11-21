"""CLI seeding — replaces the removed POST /seed endpoint (Ultra P0-2).

Run:  python -m app.cli seed
       (inside the api container: docker compose exec api python -m app.cli seed)
"""
import argparse

from .db import Chunk, ChunkEmbedding, Document, SessionLocal, init_db
from .embeddings import get_embedder
from .seed import SEED_DOCS, chunk_text


def seed():
    init_db()
    emb = get_embedder()
    session = SessionLocal()
    try:
        session.query(ChunkEmbedding).delete()
        session.query(Chunk).delete()
        session.query(Document).delete()
        session.commit()
        total_chunks = 0
        for doc in SEED_DOCS:
            d = Document(
                title=doc["title"],
                department=doc["department"],
                allowed_roles=[r.lower() for r in doc["allowed_roles"]],
                content=doc["content"],
            )
            session.add(d)
            session.flush()
            for i, piece in enumerate(chunk_text(doc["content"], title=doc["title"])):
                c = Chunk(
                    doc_id=d.id, chunk_index=i, section_path=piece["section_path"],
                    chunk_text=piece["text"], allowed_roles=d.allowed_roles,
                )
                session.add(c)
                session.flush()
                session.add(ChunkEmbedding(
                    chunk_id=c.id, model_id=emb.model_id,
                    allowed_roles=d.allowed_roles, embedding=emb.embed(piece["text"]),
                ))
                total_chunks += 1
        session.commit()
        print(f"Seeded {len(SEED_DOCS)} documents, {total_chunks} chunks ({emb.model_id})")
    finally:
        session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["seed"])
    args = parser.parse_args()
    if args.command == "seed":
        seed()
