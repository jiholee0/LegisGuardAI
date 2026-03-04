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
DELETE_PATTERN = re.compile(r"(삭제한다|삭제함|삭제)")
TARGET_LOCATOR_PATTERN = re.compile(r"(제\s*\d+\s*조(?:의\s*\d+)?(?:\s*제\s*\d+\s*항)?(?:\s*제\s*\d+\s*호)?)")
KEYWORD_PATTERN = re.compile(r"[가-힣A-Za-z0-9]{2,}")
STOPWORDS = {
    "제", "조", "항", "호", "한다", "하여야", "있다", "경우", "다음", "관련", "조항", "의무", "실시",
    "포함", "정기", "절차", "대처방법", "사업주", "규정", "법", "시행규칙", "시행령",
}
MIN_VECTOR_MATCH_SCORE = 0.45
MIN_KEYWORD_OVERLAP = 1


class LawArticleMatchTool:
    def __init__(self, session_factory: Callable[[], AbstractContextManager] = SessionLocal) -> None:
        self.session_factory = session_factory
        self.settings = get_settings()
        self.embedding_provider = build_embedding_provider()
        self.chroma_client = chromadb.PersistentClient(path=self.settings.chroma_persist_dir)

    def get_collection(self):
        return self.chroma_client.get_collection(self.settings.chroma_collection_name)

    def lookup_exact(self, *, law_name: str | None, candidate: NoticeArticleCandidate):
        with self.session_factory() as session:
            article_repo = ArticleRepository(session)
            if law_name and candidate.article_no:
                exact_matches = article_repo.list_by_law_name_and_article_no(law_name=law_name, article_no=candidate.article_no)
                if exact_matches:
                    return exact_matches[0], 1.0
            return None, None

    def search_vector(self, *, collection, law_name: str | None, candidate: NoticeArticleCandidate):
        with self.session_factory() as session:
            article_repo = ArticleRepository(session)
            embedding = self.embedding_provider.embed_query(candidate.source_text)
            where = {"law_name": law_name} if law_name else None
            result = collection.query(
                query_embeddings=[embedding],
                n_results=5,
                where=where,
            )
            ids = result.get("ids", [[]])[0]
            if not ids:
                return None, None

            metadatas = result.get("metadatas", [[]])[0]
            distances = result.get("distances", [[]])[0]
            query_keywords = self._extract_keywords(candidate.source_text)

            ranked_candidates: list[tuple[float, int, object]] = []
            for index, metadata in enumerate(metadatas):
                article = article_repo.get_by_id(int(metadata["article_id"]))
                if article is None:
                    continue
                distance = float(distances[index]) if index < len(distances) else 0.0
                vector_score = round(1.0 / (1.0 + max(distance, 0.0)), 4)
                overlap_count = self._keyword_overlap_count(
                    query_keywords=query_keywords,
                    article_text=f"{article.article_title or ''} {article.normalized_text}",
                )
                combined_score = vector_score + min(overlap_count * 0.15, 0.45)
                ranked_candidates.append((combined_score, overlap_count, article))

            if not ranked_candidates:
                return None, None

            ranked_candidates.sort(
                key=lambda item: (item[0], item[1]),
                reverse=True,
            )
            best_score, overlap_count, best_article = ranked_candidates[0]
            if best_score < MIN_VECTOR_MATCH_SCORE or overlap_count < MIN_KEYWORD_OVERLAP:
                return None, None
            return best_article, round(best_score, 4)

    def _extract_keywords(self, text: str) -> set[str]:
        keywords = {normalize_text(token) for token in KEYWORD_PATTERN.findall(normalize_text(text))}
        return {token for token in keywords if token not in STOPWORDS and len(token) >= 2}

    def _keyword_overlap_count(self, *, query_keywords: set[str], article_text: str) -> int:
        if not query_keywords:
            return 0
        article_keywords = self._extract_keywords(article_text)
        return len(query_keywords & article_keywords)


class ArticleDiffTool:
    def validate_target(self, *, candidate: NoticeArticleCandidate, matched_article):
        target_locator = self._extract_target_locator(candidate)
        if matched_article is None:
            return target_locator, None, None
        if target_locator is None:
            return None, True, None

        article_text = matched_article.article_text
        paragraph_no = self._extract_paragraph_no(target_locator)
        item_no = self._extract_item_no(target_locator)

        if paragraph_no is not None and not self._paragraph_exists(article_text, paragraph_no):
            return target_locator, False, f"현행 조문에서 제{paragraph_no}항을 찾지 못함"
        if item_no is not None and not self._item_exists(article_text, item_no):
            return target_locator, False, f"현행 조문에서 제{item_no}호를 찾지 못함"
        return target_locator, True, None

    def build_base_diff(
        self,
        *,
        candidate: NoticeArticleCandidate,
        matched_article,
        match_score: float | None,
        match_method: str,
        change_type: str,
        target_locator: str | None = None,
        target_exists: bool | None = None,
        validation_message: str | None = None,
    ) -> NoticeArticleDiff:
        current_text = matched_article.article_text if matched_article is not None else None
        fact_status = "confirmed"
        if matched_article is None and change_type != "제정":
            fact_status = "unmatched"
        elif target_exists is False:
            fact_status = "invalid_target"

        before_text = current_text
        derived_after_text = self._derive_rule_based_after_text(candidate.source_text)
        after_text = derived_after_text or None
        if fact_status == "invalid_target":
            before_text = None
            after_text = None
            diff_segments = []
            numeric_changes = []
        elif change_type == "제정" and matched_article is None:
            before_text = None
            diff_segments = self._build_diff_segments("", derived_after_text)
            numeric_changes = self._extract_numeric_changes("", derived_after_text)
        else:
            diff_segments = self._build_diff_segments(normalize_text(before_text or ""), derived_after_text) if before_text else []
            numeric_changes = self._extract_numeric_changes(normalize_text(before_text or ""), derived_after_text)

        return NoticeArticleDiff(
            article_no=candidate.article_no,
            target_locator=target_locator,
            target_exists=target_exists,
            fact_status=fact_status,
            validation_message=validation_message,
            matched_law_name=matched_article.law.law_name if matched_article is not None else None,
            matched_article_no=matched_article.article_no if matched_article is not None else None,
            matched_article_key=matched_article.article_key if matched_article is not None else None,
            current_text=current_text,
            before_text=before_text,
            after_text=after_text,
            diff_summary=self._build_invalid_target_summary(target_locator, validation_message) if fact_status == "invalid_target" else None,
            labels=["대상미존재"] if fact_status == "invalid_target" else [],
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
        if DELETE_PATTERN.search(source_text):
            return ""
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


    def _extract_target_locator(self, candidate: NoticeArticleCandidate) -> str | None:
        if candidate.article_ref_text:
            locator = normalize_text(candidate.article_ref_text)
            if "제" in locator and ("항" in locator or "호" in locator):
                return locator
        match = TARGET_LOCATOR_PATTERN.search(candidate.source_text)
        if not match:
            return None
        locator = normalize_text(match.group(1))
        if "항" in locator or "호" in locator:
            return locator
        return None

    def _extract_paragraph_no(self, locator: str) -> int | None:
        match = re.search(r"제\s*(\d+)\s*항", locator)
        return int(match.group(1)) if match else None

    def _extract_item_no(self, locator: str) -> int | None:
        match = re.search(r"제\s*(\d+)\s*호", locator)
        return int(match.group(1)) if match else None

    def _paragraph_exists(self, article_text: str, paragraph_no: int) -> bool:
        marker = self._to_circled_number(paragraph_no)
        if marker is None:
            return False
        return marker in article_text

    def _item_exists(self, article_text: str, item_no: int) -> bool:
        return bool(
            re.search(
                rf"(?:(?<=\n)|^|\s){item_no}(?:\.|\))\s",
                article_text,
            )
        )

    def _to_circled_number(self, number: int) -> str | None:
        circled_numbers = {
            1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤",
            6: "⑥", 7: "⑦", 8: "⑧", 9: "⑨", 10: "⑩",
            11: "⑪", 12: "⑫", 13: "⑬", 14: "⑭", 15: "⑮",
            16: "⑯", 17: "⑰", 18: "⑱", 19: "⑲", 20: "⑳",
        }
        return circled_numbers.get(number)

    def _build_invalid_target_summary(self, target_locator: str | None, validation_message: str | None) -> str:
        if target_locator and validation_message:
            return f"{target_locator}에 대한 변경 요청이 있으나 {validation_message}"
        if target_locator:
            return f"{target_locator}에 대한 변경 요청이 있으나 현행 조문에서 대상을 찾지 못함"
        return "변경 요청 대상이 현행 조문에서 확인되지 않음"
