# Project 8 — Permission-Aware RAG

**Sales and HR ask the same question. They must never get each other's documents.**

## v0.3 — Ultra architecture review applied (18 Nov 2026)

Ultra's review ([original HTML](sandbox://workspace/user/files/architecture_security_review_permission_aware_rag_starter.html)) was strong and mostly verified. I implemented all 4 P0s — see `migrations/001_rls_hybrid.sql` and the code. 10/10 tests pass (5 permission + 5 eval T1–T5).

- **P0 Auth:** `user_id` in the request body is gone. `/search` and `/ask` now require `Authorization: Bearer <token>`; roles come from verified token claims only. Demo tokens via `POST /auth/demo-token` (disable with `ALLOW_DEMO_TOKENS=false` in prod; replace the HMAC demo verifier with PyJWT/OIDC in prod).
- **P0 Attack surface:** `POST /seed` removed — seed via CLI: `docker compose exec api python -m app.cli seed`. `GET /demo/compare` removed — the frontend now fires two separate `/ask` calls with two separate tokens. No backend dual-role route.
- **P0 Prompt injection:** passages wrapped in `<untrusted_passage>` tags, Markdown images/links stripped from answers, citations validated against the SQL result set.
- **P0 Vector schema:** hash embedder removed as default. Standard `VECTOR(384)` everywhere: default `fastembed` (ONNX bge-small-en-v1.5, ~130MB, no key) or OpenAI `text-embedding-3-small` with `dimensions=384`. Vectors decoupled into `chunk_embeddings(chunk_id, model_id)`, UUID PKs, lowercase role CHECK.
- **P1 Recall:** hybrid search — dense HNSW + lexical `tsvector`, fused by RRF, with pgvector 0.8+ iterative scan (`strict_order`). Heading-aware chunking with `[Title > Section]` prefixes, reset per document.
- **P2 Hardening:** raw float distances hidden (coarse `high/medium/low` tiers), `GET /docs/{id}` returns 404 not 403, audit log line per query.

**Verified live:** pgvector 0.8.0 iterative scans + `strict_order` / `max_scan_tuples` — confirmed. OpenAI `dimensions` param + Matryoshka 384 — confirmed, but measure recall at 384 vs 768 on your eval set.
**NOT verified — do not cite:** Ultra's Vec2Text numbers (arXiv:2611.05716, "47.4% exact recovery") could not be verified live. The mitigation (never serialize embeddings, encrypt backups) stands; the percentages don't.

**Breaking on upgrade:** run `docker compose down -v` first (UUID PKs + decoupled embeddings change the schema), then `docker compose up --build` + CLI seed.

This is the core RAG project from the 20-project portfolio: a FastAPI + PostgreSQL + pgvector search where the permission check happens **in SQL, before similarity search, before the model ever sees text**.

## The security rule (the whole point)

```
WRONG: retrieve top-k by similarity -> filter in Python -> generate
RIGHT: WHERE allowed_roles && user_roles -> similarity search -> generate
```

If you filter after retrieval, a clever query can leak a score, a snippet, or a citation from a document the user should never see. Filtering in SQL means forbidden chunks are never retrieved at all. The `/search` endpoint proves it: it returns the *actual retrieved passages* so a visitor can inspect the boundary before generation.

## Demo in 30 seconds

1. Ask as Sales: **"What discount can we offer enterprise clients?"**
   → Gets `Q4 Pricing & Discounts` + shared docs.
2. Ask the *same* question as HR:
   → Gets only shared docs. Pricing is invisible — not redacted, not summarised, never retrieved.
3. Ask as HR: **"What are the 2026 salary bands?"**
   → Gets `Salary Bands 2026`. Sales asking the same gets nothing relevant.

## Stack

FastAPI + Pydantic v2, PostgreSQL 16 + pgvector, SQLAlchemy, Docker Compose, pytest.
Embeddings are pluggable — see `app/embeddings.py`.

No API key needed to run the demo. The default embedder is a deterministic local
hash embedder (384-dim, offline). Swap it for real embeddings when you are ready:

| Provider | Env | Dim | When to use |
|---|---|---|---|
| `local` (default) | `EMBEDDING_PROVIDER=local` | 384 | Offline demo, permission logic, CI |
| `openai` | `EMBEDDING_PROVIDER=openai` + `OPENAI_API_KEY` | 1536 | Real quality — needs a key, costs tokens |
| `sentence-transformers` | `EMBEDDING_PROVIDER=st` | 384 | Real local quality, `all-MiniLM-L6-v2` |

> The local embedder is **not** a real semantic model — it is for proving the
> permission boundary. Do not ship it as "AI search". The retrieval quality
> jump when you switch to OpenAI / MiniLM is part of the learning.

## Quick start (on your PC, in a throwaway folder)

```bash
# 1. Copy env
cp .env.example .env

# 2. Start Postgres + API
docker compose up --build

# 3. Seed the 6 demo documents (2 Sales-only, 2 HR-only, 2 shared)
curl -X POST http://localhost:8000/seed

# 4. Open the demo UI
# http://localhost:8000/
# Or try the API:
curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"user_id": "sara-sales", "query": "enterprise discount", "top_k": 3}'

curl -X POST http://localhost:8000/search \
  -H "Content-Type: application/json" \
  -d '{"user_id": "layla-hr", "query": "enterprise discount", "top_k": 3}'

# Same question, both sides at once:
curl "http://localhost:8000/demo/compare?query=What%20are%20the%202026%20salary%20bands%3F"
```

## Demo users (header-free, for the portfolio demo only)

| user_id | Roles | Can see |
|---|---|---|
| `sara-sales` | sales | Sales + shared |
| `omar-sales` | sales | Sales + shared |
| `layla-hr` | hr | HR + shared |
| `ahmed-hr` | hr | HR + shared |

In production replace `app/auth.py` with JWT / OIDC — never trust a `user_id`
from the request body. The demo keeps it explicit so the boundary is easy to
inspect. See `app/auth.py` for the TODO.

## API

- `GET /health` — DB + embedding provider status
- `POST /seed` — load the 6 demo docs, chunk + embed them
- `POST /search` — `{user_id, query, top_k}` → retrieved passages **before generation**, with `doc_title`, `allowed_roles`, `score`, and `filtered_out_count`
- `POST /ask` — same retrieval, then an answer with citations. Without an LLM key it returns an extractive answer built only from the retrieved passages. With `OPENAI_API_KEY` it calls the model with *only* those passages as context.
- `GET /demo/compare?query=...` — runs the same query as Sales and HR side-by-side
- `GET /docs` — list documents + who can see them
- `GET /` — demo UI

## Seed documents

| Doc | Allowed roles | Why it matters |
|---|---|---|
| Product Overview | sales, hr | Shared — both should find it |
| Company Handbook | sales, hr | Shared |
| Q4 Pricing & Discounts | sales | Sales-only: discount tiers, floor price |
| Enterprise Client Contract Terms | sales | Sales-only: payment terms, NDA clause |
| Salary Bands 2026 | hr | HR-only: pay bands by level |
| Disciplinary Policy | hr | HR-only: process, warnings |

## Tests

```bash
docker compose exec api pytest -v
# or locally, without Docker:
pip install -r requirements.txt
pytest -v
```

`tests/test_permissions.py` covers the rule that matters:
- Sales cannot retrieve HR chunks even when the query words match HR text exactly
- HR cannot retrieve Sales chunks
- Shared docs are retrievable by both
- The SQL filter string is built before the similarity clause

## What to show in your portfolio README / video

1. Same question, two users, two different passage lists (screenshot the `/` UI)
2. The SQL in `app/db.py` — highlight the `WHERE` clause
3. A failing test if you remove the filter (temporarily comment it out, watch the test go red, restore it)
4. The swap from `local` → `openai` embeddings and what changes in quality, not in permissions

## Next steps after this starter

1. Swap `local` → `sentence-transformers` locally, re-seed, compare retrieval quality
2. Add LangGraph: a router that decides search vs. direct answer, with the same permission filter on every tool call
3. Add real auth (Keycloak / OIDC) — this is Project 4, merged in
4. Add evaluation: 20 questions with expected doc IDs, score recall@k per role — this is Project 13, merged in

## Files

```
docker-compose.yml   Postgres (pgvector) + API
Dockerfile
app/main.py          FastAPI endpoints
app/db.py            SQLAlchemy models + the permission-filtered query
app/embeddings.py    Pluggable embedders
app/auth.py          Demo users (replace with JWT in prod)
app/seed.py          The 6 demo documents
demo/index.html      Side-by-side Sales vs HR UI
tests/test_permissions.py
ULTRA_PROMPT.md      Copy-paste prompt for Google AI Ultra architecture review
```
