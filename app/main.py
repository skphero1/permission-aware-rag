"""Permission-Aware RAG v0.3 — FastAPI app.

P0 fixes applied from Ultra's review:
  - No `user_id` in request bodies. Roles come from a Bearer token only.
  - POST /seed REMOVED (use `python -m app.cli seed`). No unauthenticated
    caller can wipe / overwrite allowed_roles mid-demo.
  - GET /demo/compare REMOVED. It was a backend dual-role god-mode route.
    The frontend now fires two separate POST /ask calls with two separate
    demo tokens (Sales persona vs HR persona).
  - /ask wraps passages in <untrusted_passage>, strips Markdown images/links,
    validates citations against the SQL result, and logs an audit row.
  - Responses return coarse relevance tiers, never raw float distances.
  - GET /docs/{id} returns 404 (not 403) when RLS hides the document,
    so its existence is not confirmed to an unauthorised caller.
"""
import logging
import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from .auth import ALLOW_DEMO_TOKENS, create_demo_token, get_user_from_token
from .db import (
    Document,
    SessionLocal,
    count_inaccessible_chunks,
    hybrid_search,
    init_db,
)
from .embeddings import get_embedder
from .sanitize import strip_markdown_images_and_links, validate_citations, wrap_passages

app = FastAPI(title="Permission-Aware RAG — Project 8 (v0.3)")
security = HTTPBearer(auto_error=False)
log = logging.getLogger("rag.audit")


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5


class AskRequest(BaseModel):
    query: str
    top_k: int = 5


class DemoTokenRequest(BaseModel):
    user_id: str


def current_user(creds: HTTPAuthorizationCredentials | None = Depends(security)):
    if not creds or creds.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    try:
        return get_user_from_token(creds.credentials)
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


def _search(user: dict, query: str, top_k: int):
    emb = get_embedder()
    qvec = emb.embed(query)
    session = SessionLocal()
    try:
        # One transaction: set_config RLS context + hybrid search inside it.
        with session.begin():
            passages = hybrid_search(session, qvec, query, user["roles"], emb.model_id, top_k)
            hidden = count_inaccessible_chunks(session, user["roles"])
    finally:
        session.close()
    log.info(
        "audit user=%s roles=%s query=%r chunks=%s",
        user["user_id"], user["roles"], query,
        [p["chunk_id"] for p in passages],
    )
    return {"user": {"user_id": user["user_id"], "roles": user["roles"]},
            "query": query, "passages": passages, "filtered_out_chunks": hidden}


@app.get("/health")
def health():
    emb = get_embedder()
    return {"status": "ok", "embedding_provider": emb.name, "dim": emb.dim,
            "auth": "bearer-required", "demo_tokens": ALLOW_DEMO_TOKENS}


@app.get("/")
def demo_ui():
    return FileResponse(os.path.join(os.path.dirname(__file__), "..", "demo", "index.html"))


@app.post("/auth/demo-token")
def demo_token(req: DemoTokenRequest):
    """Dev-only token minting. Disable with ALLOW_DEMO_TOKENS=false in prod."""
    if not ALLOW_DEMO_TOKENS:
        raise HTTPException(status_code=403, detail="Demo tokens are disabled")
    try:
        return {"access_token": create_demo_token(req.user_id), "token_type": "bearer"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/docs")
def list_docs(user: dict = Depends(current_user)):
    init_db()
    session = SessionLocal()
    try:
        with session.begin():
            from .db import set_rls_context

            set_rls_context(session, user["roles"])
            docs = session.query(Document).all()
            return [
                {"id": str(d.id), "title": d.title, "department": d.department,
                 "allowed_roles": d.allowed_roles}
                for d in docs
            ]
    finally:
        session.close()


@app.get("/docs/{doc_id}")
def get_doc(doc_id: str, user: dict = Depends(current_user)):
    """404, not 403, when RLS hides the document — existence is not leaked."""
    from uuid import UUID

    session = SessionLocal()
    try:
        with session.begin():
            from .db import set_rls_context

            set_rls_context(session, user["roles"])
            try:
                uid = UUID(doc_id)
            except ValueError:
                raise HTTPException(status_code=404, detail="Document not found")
            doc = session.query(Document).filter(Document.id == uid).first()
            if not doc:
                raise HTTPException(status_code=404, detail="Document not found")
            return {"id": str(doc.id), "title": doc.title,
                    "department": doc.department, "allowed_roles": doc.allowed_roles}
    finally:
        session.close()


@app.post("/search")
def search(req: SearchRequest, user: dict = Depends(current_user)):
    return _search(user, req.query, req.top_k)


@app.post("/ask")
def ask(req: AskRequest, user: dict = Depends(current_user)):
    result = _search(user, req.query, req.top_k)
    passages = result["passages"]
    if not passages:
        return {**result, "answer": "I don't have access to any documents that answer that question.",
                "citations": []}
    # Citations validated against the SQL result — a model cannot invent one.
    allowed_ids = [p["chunk_id"] for p in passages]
    citations = [
        {"chunk_id": p["chunk_id"], "doc_title": p["doc_title"], "text": p["text"]}
        for p in passages
        if p["chunk_id"] in validate_citations([p["chunk_id"] for p in passages], allowed_ids)
    ]
    # Extractive answer from retrieved passages only. The <untrusted_passage>
    # wrapping is the context format a real LLM call must use — see sanitize.py.
    _model_context = wrap_passages(passages)
    answer = (
        f"Based on {len(passages)} document passage(s) you can access:\n\n"
        + "\n\n".join(f"[{p['doc_title']}] {p['text']}" for p in passages)
    )
    answer = strip_markdown_images_and_links(answer)
    return {**result, "answer": answer, "citations": citations,
            "model_context_format": "untrusted_passage tags (see _model_context in server logs, never sent raw)"}
