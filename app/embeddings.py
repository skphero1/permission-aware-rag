"""Pluggable embeddings v0.3 — hash embedder REMOVED as default (Ultra P0-4).

The v0.2 local hash embedder mapped synonyms like "compensation" and
"salary" to orthogonal coordinates, so offline search was worse than ILIKE.
It is kept ONLY behind EMBEDDING_PROVIDER=hash-demo, with a loud warning,
for the permission-logic tests. Never ship it as search.

Standard: VECTOR(384) for every provider, so one schema fits all.
- fastembed (default): ONNX BAAI/bge-small-en-v1.5, 384-dim, ~130MB RAM,
  no API key, no PyTorch. `pip install fastembed`
- openai: text-embedding-3-small with dimensions=384 (Matryoshka — verified:
  OpenAI exposes a `dimensions` parameter; 384 is allowed, but measure
  recall at 384 vs 768 on your eval set, quality drops as you shrink).
- st: sentence-transformers all-MiniLM-L6-v2, 384-dim (heavier, ~2.5GB with torch)

Each embedder reports model_id, stored in chunk_embeddings.model_id so a
provider swap never mixes vectors from different models in one query.
"""
import hashlib
import math
import os
import warnings

DIM = 384


class FastEmbedEmbedder:
    dim = DIM
    model_id = "fastembed:bge-small-en-v1.5:384"
    name = "fastembed bge-small-en-v1.5 (ONNX, local)"

    def __init__(self):
        from fastembed import TextEmbedding  # pip install fastembed

        self.model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [list(map(float, v)) for v in self.model.embed(texts)]


class OpenAIEmbedder:
    dim = DIM
    model_id = "openai:text-embedding-3-small:384"
    name = "openai text-embedding-3-small @384"

    def __init__(self, api_key: str | None = None):
        from openai import OpenAI  # pip install openai

        key = api_key or os.environ.get("OPENAI_API_KEY")
        if not key:
            raise ValueError("OPENAI_API_KEY is required for EMBEDDING_PROVIDER=openai")
        self.client = OpenAI(api_key=key)

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        resp = self.client.embeddings.create(
            model="text-embedding-3-small", input=texts, dimensions=DIM
        )
        return [list(d.embedding) for d in resp.data]


class SentenceTransformerEmbedder:
    dim = DIM
    model_id = "st:all-MiniLM-L6-v2:384"
    name = "sentence-transformers all-MiniLM-L6-v2"

    def __init__(self):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer("all-MiniLM-L6-v2")

    def embed(self, text: str) -> list[float]:
        return self.embed_many([text])[0]

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return self.model.encode(texts, normalize_embeddings=True).tolist()


def _hash_embed(text: str, dim: int = DIM) -> list[float]:
    vec = [0.0] * dim
    for word in text.lower().split():
        h = int(hashlib.md5(word.encode()).hexdigest(), 16)
        vec[h % dim] += 1.0
        vec[(h // dim) % dim] += 0.5
    norm = math.sqrt(sum(x * x for x in vec)) or 1.0
    return [x / norm for x in vec]


class HashDemoEmbedder:
    """Demo-only. NOT semantic search. Kept so permission tests run offline."""

    dim = DIM
    model_id = "hash-demo:NOT-SEMANTIC:384"
    name = "hash-demo (NOT semantic — permission tests only)"

    def embed(self, text: str) -> list[float]:
        return _hash_embed(text, self.dim)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


def get_embedder():
    provider = os.environ.get("EMBEDDING_PROVIDER", "fastembed").lower()
    if provider == "openai":
        return OpenAIEmbedder()
    if provider == "st":
        return SentenceTransformerEmbedder()
    if provider == "hash-demo":
        warnings.warn(
            "EMBEDDING_PROVIDER=hash-demo is NOT semantic search. "
            "Use it only for permission-logic tests.",
            UserWarning,
        )
        return HashDemoEmbedder()
    if provider == "fastembed":
        try:
            return FastEmbedEmbedder()
        except ImportError as e:
            raise ImportError(
                "fastembed is not installed. Run: pip install fastembed "
                "— or set EMBEDDING_PROVIDER=openai / hash-demo."
            ) from e
    raise ValueError(f"Unknown EMBEDDING_PROVIDER '{provider}'")
