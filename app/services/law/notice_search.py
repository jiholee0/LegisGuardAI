from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Callable
import re

import chromadb
from fastapi import HTTPException

from app.core.config import get_settings
from app.db.repositories.articles import ArticleRepository
from app.db.session import SessionLocal
from app.schemas.search import NoticeSearchMatch, NoticeSearchRequest, NoticeSearchResponse, NoticeSearchUnit
from app.services.embeddings import build_embedding_provider
from app.services.text_normalizer import normalize_text


LAW_NAME_PATTERN = re.compile(r"(산업안전보건법(?: 시행령| 시행규칙)?)")
QUERY_SPLIT_PATTERN = re.compile(r"[\n\r]+|(?<=[.!?])\s+")
QUOTED_TEXT_PATTERN = re.compile(r'"([^"]+)"|\'([^\']+)\'')
ARTICLE_REFERENCE_PATTERN = re.compile(r"제\s*\d+\s*조(?:의\s*\d+)?(?:\s*제\s*\d+\s*항)?")


class NoticeSearchService:
    def __init__(self, session_factory: Callable[[], AbstractContextManager] = SessionLocal) -> None:
        self.session_factory = session_factory
        self.settings = get_settings()
        self.embedding_provider = build_embedding_provider()
        self.chroma_client = chromadb.PersistentClient(path=self.settings.chroma_persist_dir)

    def search_notice(self, payload: NoticeSearchRequest) -> NoticeSearchResponse:
        try:
            collection = self.chroma_client.get_collection(self.settings.chroma_collection_name)
        except Exception as exc:
            raise HTTPException(status_code=409, detail="Vector index is not initialized.") from exc

        if collection.count() == 0:
            raise HTTPException(status_code=409, detail="Vector index is empty. Run /admin/reindex first.")

        text = self._extract_text(payload)
        law_name = self._extract_law_name(payload, text)
        query_units = self._split_query_units(text)

        with self.session_factory() as session:
            article_repo = ArticleRepository(session)
            response_units: list[NoticeSearchUnit] = []
            for unit in query_units:
                matches = self._find_matches_for_unit(
                    collection=collection,
                    article_repo=article_repo,
                    law_name=law_name,
                    unit=unit,
                    top_k=payload.top_k,
                )
                response_units.append(NoticeSearchUnit(query_text=unit, matches=matches))

        return NoticeSearchResponse(query_units=response_units)

    def _extract_text(self, payload: NoticeSearchRequest) -> str:
        if payload.input_type == "json" and payload.body_json:
            return normalize_text(payload.body_json.content)
        return normalize_text(payload.body or "")

    def _extract_law_name(self, payload: NoticeSearchRequest, text: str) -> str | None:
        if payload.input_type == "json" and payload.body_json and payload.body_json.law_name:
            return payload.body_json.law_name
        match = LAW_NAME_PATTERN.search(f"{payload.title or ''} {text}")
        return match.group(1) if match else None

    def _split_query_units(self, text: str) -> list[str]:
        normalized_text = normalize_text(text)
        units: list[str] = []

        if "→" in text or "->" in text:
            for raw_part in re.split(r"→|->", text):
                candidate = normalize_text(raw_part)
                if candidate:
                    units.append(candidate)

        for match in QUOTED_TEXT_PATTERN.finditer(text):
            candidate = normalize_text(match.group(1) or match.group(2) or "")
            if candidate:
                units.append(candidate)

        for match in ARTICLE_REFERENCE_PATTERN.finditer(text):
            candidate = normalize_text(match.group(0))
            if candidate:
                units.append(candidate)

        for raw_part in QUERY_SPLIT_PATTERN.split(text):
            candidate = normalize_text(raw_part)
            if candidate:
                units.append(candidate)

        if normalized_text and normalized_text not in units:
            units.append(normalized_text)

        deduped_units: list[str] = []
        seen: set[str] = set()
        for unit in units:
            if unit in seen:
                continue
            if len(unit) < 2:
                continue
            seen.add(unit)
            deduped_units.append(unit)

        return deduped_units or [normalized_text]

    def _find_matches_for_unit(
        self,
        collection,
        article_repo: ArticleRepository,
        law_name: str | None,
        unit: str,
        top_k: int,
    ) -> list[NoticeSearchMatch]:
        matches: list[NoticeSearchMatch] = []
        seen_articles: set[int] = set()

        exact_article_no = self._extract_article_no(unit)
        if law_name and exact_article_no:
            for article in article_repo.list_by_law_name_and_article_no(law_name=law_name, article_no=exact_article_no):
                matches.append(
                    NoticeSearchMatch(
                        law_name=article.law.law_name,
                        article_no=article.article_no,
                        article_title=article.article_title,
                        article_text=article.article_text,
                        score=1.0,
                        article_key=article.article_key,
                    )
                )
                seen_articles.add(article.id)
                if len(matches) >= top_k:
                    return matches

        embedding = self.embedding_provider.embed_query(unit)
        where = {"law_name": law_name} if law_name else None
        result = collection.query(
            query_embeddings=[embedding],
            n_results=max(top_k * 3, top_k),
            where=where,
        )
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        metadatas = result.get("metadatas", [[]])[0]

        for index, _ in enumerate(ids):
            metadata = metadatas[index]
            article_id = int(metadata["article_id"])
            if article_id in seen_articles:
                continue
            article = article_repo.get_by_id(article_id)
            if article is None:
                continue
            distance = float(distances[index]) if index < len(distances) else 0.0
            score = 1.0 / (1.0 + max(distance, 0.0))
            matches.append(
                NoticeSearchMatch(
                    law_name=article.law.law_name,
                    article_no=article.article_no,
                    article_title=article.article_title,
                    article_text=article.article_text,
                    score=round(score, 4),
                    article_key=article.article_key,
                )
            )
            seen_articles.add(article_id)
            if len(matches) >= top_k:
                break

        return matches

    def _extract_article_no(self, text: str) -> str | None:
        match = re.search(r"제\s*(\d+)\s*조(?:의\s*(\d+))?", text)
        if not match:
            return None

        article_no = f"제{match.group(1)}조"
        if match.group(2):
            article_no += f"의{match.group(2)}"
        return article_no
