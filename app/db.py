"""Postgres + pgvector models and permission-filtered retrieval v0.3.

Defense in depth (Ultra P0-1, verified against pgvector docs):
  Layer 1 — SQL WHERE `allowed_roles && :roles` before similarity ordering.
  Layer 2 — PostgreSQL FORCE ROW LEVEL SECURITY (see migrations/001_rls_hybrid.sql),
            bound per-transaction via set_config('app.user_roles', ..., true).
            Even if a future endpoint forgets the WHERE, RLS still blocks the row.
            `set_config(..., true)` scopes to the current transaction only, so
            a pooled connection cannot leak HR roles to the next caller.
  Layer 3 — No raw distances leave the API (see relevance_tier). Exact floats
            are an embedding oracle / timing side-channel (Ultra P2-5).

Schema changes vs v0.2 (BREAKING — run `docker compose down -v` on upgrade):
  - UUID primary keys (no sequential IDs leaking corpus size)
  - Vectors decoupled into chunk_embeddings(chunk_id, model_id, embedding)
    so a provider swap backfills in the background instead of rewriting
    200k rows in place (MVCC dead-tuple storm)
  - allowed_roles has a lowercase CHECK constraint
  - Hybrid search: dense HNSW + lexical tsvector fused by Reciprocal Rank
    Fusion, with pgvector 0.8+ iterative scan (SET LOCAL hnsw.iterative_scan
    = 'strict_order') so a filtered HNSW scan fills the full k budget.
"""
import os
import uuid

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    CheckConstraint,
    Column,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

from .permissions import is_allowed  # re-exported

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql+psycopg://rag:ragpassword@localhost:5432/ragdb"
)
EMBEDDING_DIM = 384  # standardised v0.3: every provider is 384-dim

Base = declarative_base()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "cardinality(allowed_roles) > 0 AND allowed_roles <@ ARRAY['sales','hr','admin']::TEXT[]",
            name="ck_documents_roles",
        ),
    )
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    title = Column(String(200), nullable=False)
    department = Column(String(50), nullable=False)
    allowed_roles = Column(ARRAY(String), nullable=False)
    content = Column(Text, nullable=False)
    chunks = relationship("Chunk", back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"
    id = Column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doc_id = Column(PGUUID(as_uuid=True), ForeignKey("documents.id"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    section_path = Column(Text, nullable=False, default="")
    chunk_text = Column(Text, nullable=False)
    allowed_roles = Column(ARRAY(String), nullable=False)
    document = relationship("Document", back_populates="chunks")
    embeddings = relationship("ChunkEmbedding", back_populates="chunk", cascade="all, delete-orphan")


class ChunkEmbedding(Base):
    __tablename__ = "chunk_embeddings"
    chunk_id = Column(PGUUID(as_uuid=True), ForeignKey("chunks.id"), primary_key=True)
    model_id = Column(String(100), primary_key=True)
    allowed_roles = Column(ARRAY(String), nullable=False)
    embedding = Column(Vector(EMBEDDING_DIM), nullable=False)
    chunk = relationship("Chunk", back_populates="embeddings")


engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


def init_db():
    with engine.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.execute(text('CREATE EXTENSION IF NOT EXISTS "pgcrypto"'))
    Base.metadata.create_all(engine)


def set_rls_context(session, roles: list[str]):
    """Bind roles for THIS transaction only. Must be called inside a
    transaction, before any SELECT. The `true` flag is transaction-local."""
    roles_csv = ",".join(sorted(set(r.lower() for r in roles)))
    session.execute(
        text("SELECT set_config('app.user_roles', :roles, true)"),
        {"roles": roles_csv},
    )


def relevance_tier(distance: float) -> str:
    """Coarse tier instead of the raw float — no embedding oracle."""
    similarity = 1.0 - float(distance)
    if similarity >= 0.65:
        return "high"
    if similarity >= 0.35:
        return "medium"
    return "low"


def permission_filtered_search(session, query_embedding: list[float], roles: list[str], top_k: int = 5):
    """Dense-only search (kept for compatibility). Prefer hybrid_search."""
    set_rls_context(session, roles)
    session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
    session.execute(text("SET LOCAL hnsw.ef_search = 64"))
    sql = text(
        """
        SELECT c.id, c.chunk_text, d.title, d.allowed_roles,
               ce.embedding <=> CAST(:q AS vector) AS distance
        FROM chunk_embeddings ce
        JOIN chunks c ON c.id = ce.chunk_id
        JOIN documents d ON d.id = c.doc_id
        WHERE d.allowed_roles && CAST(:roles AS text[])
          AND ce.allowed_roles && CAST(:roles AS text[])
          AND (ce.embedding <=> CAST(:q AS vector)) <= 0.65
        ORDER BY distance ASC
        LIMIT :top_k
        """
    )
    rows = session.execute(
        sql, {"q": str(query_embedding), "roles": roles, "top_k": top_k}
    ).fetchall()
    return [
        {
            "chunk_id": str(r[0]),
            "text": r[1],
            "doc_title": r[2],
            "allowed_roles": r[3],
            "relevance": relevance_tier(r[4]),
        }
        for r in rows
    ]


def hybrid_search(session, query_embedding: list[float], query_text: str, roles: list[str], model_id: str, top_k: int = 5):
    """Dense HNSW + lexical tsvector, fused by Reciprocal Rank Fusion.

    RRF score = 1/(60 + rank_dense) + 1/(60 + rank_lexical). Lexical wins on
    exact codes / names / policy IDs (e.g. HR-EQ-409A); dense wins on meaning.
    Both CTEs carry the same allowed_roles filter + RLS context.
    """
    set_rls_context(session, roles)
    session.execute(text("SET LOCAL hnsw.iterative_scan = 'strict_order'"))
    session.execute(text("SET LOCAL hnsw.ef_search = 64"))
    session.execute(text("SET LOCAL hnsw.max_scan_tuples = 20000"))
    sql = text(
        """
        WITH dense_hits AS (
          SELECT ce.chunk_id,
                 ROW_NUMBER() OVER (ORDER BY ce.embedding <=> CAST(:q AS vector)) AS r_dense
          FROM chunk_embeddings ce
          WHERE ce.model_id = :model_id
            AND ce.allowed_roles && CAST(:roles AS text[])
            AND (ce.embedding <=> CAST(:q AS vector)) <= 0.65
          ORDER BY ce.embedding <=> CAST(:q AS vector)
          LIMIT 40
        ), lexical_hits AS (
          SELECT c.id AS chunk_id,
                 ROW_NUMBER() OVER (
                   ORDER BY ts_rank_cd(to_tsvector('english', c.section_path || ' ' || c.chunk_text),
                     websearch_to_tsquery('english', :query_text)) DESC) AS r_lex
          FROM chunks c
          WHERE c.allowed_roles && CAST(:roles AS text[])
            AND to_tsvector('english', c.section_path || ' ' || c.chunk_text)
                @@ websearch_to_tsquery('english', :query_text)
          LIMIT 40
        ), rrf AS (
          SELECT COALESCE(d.chunk_id, l.chunk_id) AS chunk_id,
                 COALESCE(1.0 / (60 + d.r_dense), 0.0)
                 + COALESCE(1.0 / (60 + l.r_lex), 0.0) AS rrf_score
          FROM dense_hits d FULL OUTER JOIN lexical_hits l ON d.chunk_id = l.chunk_id
        )
        SELECT c.id, c.chunk_text, d.title, d.allowed_roles, rrf.rrf_score
        FROM rrf
        JOIN chunks c ON c.id = rrf.chunk_id
        JOIN documents d ON d.id = c.doc_id
        ORDER BY rrf.rrf_score DESC
        LIMIT :top_k
        """
    )
    rows = session.execute(
        sql,
        {"q": str(query_embedding), "query_text": query_text, "roles": roles,
         "model_id": model_id, "top_k": top_k},
    ).fetchall()
    return [
        {
            "chunk_id": str(r[0]),
            "text": r[1],
            "doc_title": r[2],
            "allowed_roles": r[3],
            "relevance": "high" if float(r[4]) >= 0.03 else "medium",
        }
        for r in rows
    ]


def count_inaccessible_chunks(session, roles: list[str]) -> int:
    sql = text(
        "SELECT COUNT(*) FROM chunks c JOIN documents d ON d.id = c.doc_id "
        "WHERE NOT (d.allowed_roles && CAST(:roles AS text[]))"
    )
    return int(session.execute(sql, {"roles": roles}).scalar() or 0)


__all__ = ["Document", "Chunk", "ChunkEmbedding", "engine", "SessionLocal",
           "init_db", "set_rls_context", "relevance_tier",
           "permission_filtered_search", "hybrid_search",
           "count_inaccessible_chunks", "is_allowed"]
