from __future__ import annotations

import json

from app.core.config import get_settings
from app.db.models import Article
from app.services.text_normalizer import normalize_text


def chunk_article(article: Article) -> list[dict]:
    settings = get_settings()
    if len(article.normalized_text) <= settings.article_chunk_char_limit:
        return [
            {
                "id": f"article:{article.id}:0",
                "text": article.normalized_text,
                "display_text": article.article_text,
                "chunk_type": "article",
                "chunk_order": 0,
            }
        ]

    if not article.paragraph_json:
        return [
            {
                "id": f"article:{article.id}:0",
                "text": article.normalized_text,
                "display_text": article.article_text,
                "chunk_type": "article",
                "chunk_order": 0,
            }
        ]

    paragraphs = json.loads(article.paragraph_json)
    chunks = []
    for index, paragraph in enumerate(paragraphs):
        chunks.append(
            {
                "id": f"article:{article.id}:{index}",
                "text": normalize_text(paragraph["text"]),
                "display_text": paragraph["text"],
                "chunk_type": "paragraph",
                "chunk_order": index,
            }
        )
    return chunks
