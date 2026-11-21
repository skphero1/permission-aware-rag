# Ultra prompt — Project 8 architecture review

Copy-paste this into Google AI Ultra (Gemini). Use it for the hard
architecture thinking. Implementation / bug fixing stays with Muse.

---

Review the architecture of my Permission-Aware RAG starter (FastAPI + PostgreSQL + pgvector).

Context:
- Single developer, portfolio + sellable RAG product for small agency clients (Sysfortech).
- Stack: Python 3.12, FastAPI, Pydantic v2, SQLAlchemy, pgvector, Docker Compose.
- Users: Sales and HR roles. Documents have `allowed_roles` (Postgres text[]).
- Current rule: `WHERE allowed_roles && user_roles` in SQL BEFORE cosine similarity ordering (`embedding <=> query`), so forbidden chunks are never retrieved.
- Embeddings: pluggable — local hash embedder for offline demo (384-dim), OpenAI text-embedding-3-small (1536-dim), sentence-transformers all-MiniLM-L6-v2 (384-dim).
- Endpoints: POST /seed, POST /search (returns raw passages), POST /ask (extractive answer from retrieved passages only), GET /demo/compare (same query as Sales vs HR).
- Auth today is demo-only (user_id from request body). Production will be JWT/OIDC.

Review and be specific — do not just praise it:

1. **Security holes:** Where can permissions still leak? (prompt injection in retrieved text, citation leakage, timing / distance leakage, role confusion, chunk overlap across docs, embedding inversion?) Rank by severity and give a fix for each.
2. **Retrieval quality:** Is cosine on pgvector + naive fixed-size chunking good enough for a portfolio demo? What is the smallest change that most improves recall — hybrid search, reranking, better chunking, metadata filters?
3. **Scale:** At 10k documents, what breaks first — embedding dimension mismatch, index choice (IVFFlat vs HNSW), connection handling, re-embedding on provider swap? Give concrete pgvector index DDL.
4. **Agent upgrade:** How would you add a LangGraph agent on top WITHOUT breaking the permission rule — i.e. every tool call re-applies the same filter? Sketch the node/edge design in 10 lines.
5. **What would you cut or change** before I show this to a client? Be blunt.

Deliver: a ranked fix list (P0/P1/P2), the corrected SQL + index, and a 5-test evaluation set (question → expected doc → role that must NOT see it).

---

## How to use the answer

- P0 fixes go into `app/db.py` / `app/main.py` first — bring them back to Muse and I will implement them.
- Do NOT let Ultra rewrite the whole repo. Architecture + ranked fixes only.
- Ultra quota note: this is one hard question — expect ~20-25% of short-term quota. Don't ask it to also build the Next.js frontend in the same run.
