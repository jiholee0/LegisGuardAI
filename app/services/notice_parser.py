from __future__ import annotations

import re

from app.schemas.search import NoticeArticleCandidate, NoticeParseResult, NoticeSearchRequest
from app.services.text_normalizer import normalize_text


LAW_NAME_PATTERN = re.compile(r"(산업안전보건법(?: 시행령| 시행규칙)?)")
ARTICLE_REFERENCE_PATTERN = re.compile(r"(제\s*\d+\s*조(?:의\s*\d+)?(?:\s*제\s*\d+\s*항)?)")
NEW_ARTICLE_DIRECTIVE_PATTERN = re.compile(r"^(제\s*\d+\s*조(?:의\s*\d+)?)(?:를)?\s*다음과 같이\s*신설한다\.?$")
LEADING_ARTICLE_PATTERN = re.compile(r"^(제\s*\d+\s*조(?:의\s*\d+)?)")
CHANGE_TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("전부개정", re.compile(r"(전부개정안|전부 개정|전면 개정)")),
    ("제정", re.compile(r"(제정안|신설|신규)")),
    ("폐지", re.compile(r"(폐지|삭제)")),
    ("일부개정", re.compile(r"(일부개정안|일부 개정|개정안|개정)")),
]


class NoticeParserService:
    def parse(self, payload: NoticeSearchRequest) -> NoticeParseResult:
        text = self._extract_text(payload)
        normalized_text = normalize_text(text)
        title = payload.title
        law_name = self._extract_law_name(payload, normalized_text)
        change_type = self._extract_change_type(title=title, text=normalized_text)
        article_candidates = self._extract_article_candidates(normalized_text, change_type)

        if not article_candidates and normalized_text:
            article_candidates = [
                NoticeArticleCandidate(
                    source_text=normalized_text,
                )
            ]

        return NoticeParseResult(
            doc_type=payload.input_type,
            title=title,
            law_name=law_name,
            change_type=change_type,
            normalized_text=normalized_text,
            article_candidates=article_candidates,
        )

    def _extract_text(self, payload: NoticeSearchRequest) -> str:
        if payload.input_type == "json" and payload.body_json:
            return payload.body_json.content
        return payload.body or ""

    def _extract_law_name(self, payload: NoticeSearchRequest, text: str) -> str | None:
        if payload.input_type == "json" and payload.body_json and payload.body_json.law_name:
            return payload.body_json.law_name
        match = LAW_NAME_PATTERN.search(f"{payload.title or ''} {text}")
        return match.group(1) if match else None

    def _extract_change_type(self, title: str | None, text: str) -> str:
        haystack = f"{title or ''} {text}"
        for change_type, pattern in CHANGE_TYPE_PATTERNS:
            if pattern.search(haystack):
                return change_type
        return "미상"

    def _extract_article_candidates(self, text: str, change_type: str) -> list[NoticeArticleCandidate]:
        units = self._split_units(text)
        if change_type == "제정":
            return self._extract_new_article_candidates(units)

        candidates: list[NoticeArticleCandidate] = []
        seen: set[tuple[str | None, str]] = set()

        for unit in units:
            article_refs = ARTICLE_REFERENCE_PATTERN.findall(unit)
            if not article_refs:
                continue

            for raw_article_ref in article_refs:
                article_no = self._normalize_article_no(raw_article_ref)
                key = (article_no, unit)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    NoticeArticleCandidate(
                        article_no=article_no,
                        article_ref_text=normalize_text(raw_article_ref),
                        source_text=unit,
                    )
                )

        return candidates

    def _extract_new_article_candidates(self, units: list[str]) -> list[NoticeArticleCandidate]:
        candidates: list[NoticeArticleCandidate] = []
        seen: set[tuple[str | None, str]] = set()

        for index, unit in enumerate(units):
            directive_match = NEW_ARTICLE_DIRECTIVE_PATTERN.match(unit)
            if directive_match:
                article_no = self._normalize_article_no(directive_match.group(1))
                source_text = unit
                if index + 1 < len(units):
                    next_unit = units[index + 1]
                    leading_match = LEADING_ARTICLE_PATTERN.match(next_unit)
                    next_article_no = self._normalize_article_no(leading_match.group(1)) if leading_match else None
                    if article_no and next_article_no == article_no:
                        source_text = next_unit
                key = (article_no, source_text)
                if key not in seen:
                    seen.add(key)
                    candidates.append(
                        NoticeArticleCandidate(
                            article_no=article_no,
                            article_ref_text=article_no,
                            source_text=source_text,
                        )
                    )
                continue

            leading_match = LEADING_ARTICLE_PATTERN.match(unit)
            if not leading_match:
                continue
            article_no = self._normalize_article_no(leading_match.group(1))
            key = (article_no, unit)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(
                NoticeArticleCandidate(
                    article_no=article_no,
                    article_ref_text=article_no,
                    source_text=unit,
                )
            )

        return candidates

    def _split_units(self, text: str) -> list[str]:
        parts = re.split(r"[\n\r]+|(?<=[.!?])\s+", text)
        units: list[str] = []
        for part in parts:
            candidate = normalize_text(part)
            if candidate:
                units.append(candidate)
        return units

    def _normalize_article_no(self, text: str) -> str | None:
        match = re.search(r"제\s*(\d+)\s*조(?:의\s*(\d+))?", text)
        if not match:
            return None
        article_no = f"제{match.group(1)}조"
        if match.group(2):
            article_no += f"의{match.group(2)}"
        return article_no
