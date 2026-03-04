from __future__ import annotations

from contextlib import AbstractContextManager
from difflib import SequenceMatcher
from typing import Callable
import re

import chromadb

from app.core.config import get_settings
from app.db.repositories.articles import ArticleRepository
from app.db.session import SessionLocal
from app.schemas.search import DiffHighlight, DiffSegment, NoticeArticleCandidate, NoticeArticleDiff, NumericChange
from app.services.embeddings import build_embedding_provider
from app.services.text_normalizer import normalize_text


NUMBER_PATTERN = re.compile(r"\d+(?:\.\d+)?(?:개월|일|년|회|시간|분|%|명)?")
ARTICLE_REF_CLEANUP_PATTERN = re.compile(r"제\s*\d+\s*조(?:의\s*\d+)?(?:\s*제\s*\d+\s*항)?")
QUOTED_TEXT_PATTERN = re.compile(r'"([^"]+)"|\'([^\']+)\'')


class LawArticleMatchTool:
    def __init__(self, session_factory: Callable[[], AbstractContextManager] = SessionLocal) -> None:
        self.session_factory = session_factory
        self.settings = get_settings()
        self.embedding_provider = build_embedding_provider()
        self.chroma_client = chromadb.PersistentClient(path=self.settings.chroma_persist_dir)

    def get_collection(self):
        return self.chroma_client.get_collection(self.settings.chroma_collection_name)

    def match(self, *, collection, law_name: str | None, candidate: NoticeArticleCandidate):
        with self.session_factory() as session:
            article_repo = ArticleRepository(session)
            if law_name and candidate.article_no:
                exact_matches = article_repo.list_by_law_name_and_article_no(law_name=law_name, article_no=candidate.article_no)
                if exact_matches:
                    return exact_matches[0], 1.0, "exact_article_no"

            embedding = self.embedding_provider.embed_query(candidate.source_text)
            where = {"law_name": law_name} if law_name else None
            result = collection.query(
                query_embeddings=[embedding],
                n_results=1,
                where=where,
            )
            ids = result.get("ids", [[]])[0]
            if not ids:
                return None, None, "unmatched"

            metadata = result.get("metadatas", [[]])[0][0]
            distances = result.get("distances", [[]])[0]
            article = article_repo.get_by_id(int(metadata["article_id"]))
            if article is None:
                return None, None, "unmatched"

            distance = float(distances[0]) if distances else 0.0
            score = round(1.0 / (1.0 + max(distance, 0.0)), 4)
            return article, score, "vector_search"


class ArticleDiffTool:
    def build_base_diff(
        self,
        *,
        candidate: NoticeArticleCandidate,
        matched_article,
        match_score: float | None,
        match_method: str,
    ) -> NoticeArticleDiff:
        current_text = matched_article.article_text if matched_article is not None else None
        before_text = current_text
        after_text = self._derive_rule_based_after_text(candidate.source_text)
        diff_segments = self._build_diff_segments(normalize_text(before_text or ""), after_text) if before_text and after_text else []
        numeric_changes = self._extract_numeric_changes(normalize_text(before_text or ""), after_text)

        return NoticeArticleDiff(
            article_no=candidate.article_no,
            matched_law_name=matched_article.law.law_name if matched_article is not None else None,
            matched_article_no=matched_article.article_no if matched_article is not None else None,
            matched_article_key=matched_article.article_key if matched_article is not None else None,
            current_text=current_text,
            before_text=before_text,
            after_text=after_text,
            diff_summary=None,
            labels=[],
            match_score=match_score,
            match_method=match_method,
            analysis_method="rule_based",
            diff_segments=diff_segments,
            highlights=self._build_highlights(diff_segments),
            numeric_changes=numeric_changes,
            source_text=candidate.source_text,
        )

    def _build_diff_segments(self, before_text: str, after_text: str) -> list[DiffSegment]:
        before_tokens = self._tokenize(before_text)
        after_tokens = self._tokenize(after_text)
        matcher = SequenceMatcher(a=before_tokens, b=after_tokens)

        segments: list[DiffSegment] = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "replace":
                deleted = " ".join(before_tokens[i1:i2]).strip()
                inserted = " ".join(after_tokens[j1:j2]).strip()
                if deleted:
                    segments.append(DiffSegment(op="delete", text=deleted))
                if inserted:
                    segments.append(DiffSegment(op="insert", text=inserted))
                continue

            text = " ".join(before_tokens[i1:i2] if tag != "insert" else after_tokens[j1:j2]).strip()
            if not text:
                continue
            if tag == "equal":
                segments.append(DiffSegment(op="equal", text=text))
            elif tag == "delete":
                segments.append(DiffSegment(op="delete", text=text))
            elif tag == "insert":
                segments.append(DiffSegment(op="insert", text=text))
        return segments

    def _extract_numeric_changes(self, before_text: str, after_text: str) -> list[NumericChange]:
        before_numbers = NUMBER_PATTERN.findall(self._cleanup_numeric_text(before_text))
        after_numbers = NUMBER_PATTERN.findall(self._cleanup_numeric_text(after_text))
        changes: list[NumericChange] = []

        for before, after in zip(before_numbers, after_numbers):
            if before != after:
                changes.append(NumericChange(field=None, before=before, after=after))

        if len(after_numbers) > len(before_numbers):
            for after in after_numbers[len(before_numbers):]:
                changes.append(NumericChange(field=None, before="", after=after))

        if len(before_numbers) > len(after_numbers):
            for before in before_numbers[len(after_numbers):]:
                changes.append(NumericChange(field=None, before=before, after=""))

        return changes

    def _tokenize(self, text: str) -> list[str]:
        normalized = normalize_text(text)
        return re.findall(r"\d+(?:\.\d+)?(?:개월|일|년|회|시간|분|%)?|[가-힣A-Za-z]+|[^\s]", normalized)

    def _cleanup_numeric_text(self, text: str) -> str:
        return ARTICLE_REF_CLEANUP_PATTERN.sub(" ", normalize_text(text))

    def _derive_rule_based_after_text(self, source_text: str) -> str:
        quoted_texts = [
            normalize_text(match.group(1) or match.group(2) or "")
            for match in QUOTED_TEXT_PATTERN.finditer(source_text)
        ]
        quoted_texts = [text for text in quoted_texts if text]
        if len(quoted_texts) >= 2:
            return quoted_texts[-1]
        if quoted_texts:
            return quoted_texts[0]
        return normalize_text(source_text)

    def _build_highlights(self, segments: list[DiffSegment]) -> list[DiffHighlight]:
        highlights: list[DiffHighlight] = []
        index = 0
        while index < len(segments):
            segment = segments[index]
            next_segment = segments[index + 1] if index + 1 < len(segments) else None
            if segment.op == "delete" and next_segment and next_segment.op == "insert":
                highlights.append(DiffHighlight(type="replace", before=segment.text, after=next_segment.text))
                index += 2
                continue
            if segment.op == "delete":
                highlights.append(DiffHighlight(type="delete", before=segment.text, after=None))
            elif segment.op == "insert":
                highlights.append(DiffHighlight(type="insert", before=None, after=segment.text))
            index += 1
        return highlights
