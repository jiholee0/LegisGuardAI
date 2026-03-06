from __future__ import annotations

import json
import logging
from typing import Any

from app.clients.llm_client import LlmClient, build_llm_client
from app.schemas.search import NoticeArticleCandidate, NoticeParseResult, NoticeSearchRequest
from app.services.agents.tools.pdf_image_converter import PdfImageConverterTool
from app.services.text_normalizer import normalize_text

logger = logging.getLogger(__name__)


NOTICE_PARSE_SYSTEM_PROMPT = """
당신은 LegisGuard-Orchestrator의 입력 구조화 보조 모델이다.
입력 입법예고 문서에서 아래 JSON 스키마로만 응답한다.

키:
- law_name: 문자열 또는 null
- change_types: ["일부개정" | "전부개정" | "제정" | "폐지" | "미상", ...] (문서 전체에 존재하는 유형 집합)
- analysis_mode: "DIFF" | "STRUCTURE" | "MIXED" (문서 전체 관점)
- article_candidates: 배열
  - article_no: 문자열 또는 null (예: "제23조", "제8조의2")
  - article_ref_text: 문자열 또는 null (예: "제23조 제2항")
  - change_type: "일부개정" | "전부개정" | "제정" | "폐지" | "미상"
  - analysis_mode: "DIFF" | "STRUCTURE" (후보 단위)
  - source_text: 문자열 (후보 원문)

규칙:
- 법적 해석/평가를 쓰지 말고 구조 사실만 반환한다.
- 모르면 null 또는 "미상"을 쓴다.
- 주어진 input의 모든 정보를 활용하고, candidates에 누락이 되지 않게끔 최대한 많은 조문을 뽑아낸다.
- 별첨과 서식, 별지 등의 정보는 조문이 아니므로 candidates에 넣지 않는다.
- 각 candidate의 source_text는 원문 구간을 그대로 넣는다. 공백/구두점/개행/기호를 정규화하거나 수정하지 않는다.
""".strip()


class LlmNoticeParserTool:
    def __init__(
        self,
        llm_client: LlmClient | None = None,
        pdf_image_converter: PdfImageConverterTool | None = None,
    ) -> None:
        self.llm_client = llm_client or build_llm_client()
        self.pdf_image_converter = pdf_image_converter or PdfImageConverterTool()

    def parse(self, *, payload: NoticeSearchRequest, image_data_urls: list[str] | None = None) -> NoticeParseResult:
        source_text = self._extract_source_text(payload)
        user_prompt = self._build_user_prompt(payload=payload, source_text=source_text)
        try:
            if payload.input_type == "pdf" and hasattr(self.llm_client, "generate_json_with_images"):
                if image_data_urls is None and payload.raw_pdf_base64:
                    image_data_urls = self.pdf_image_converter.convert(pdf_base64=payload.raw_pdf_base64)
                if image_data_urls:
                    raw = self.llm_client.generate_json_with_images(
                        system_prompt=NOTICE_PARSE_SYSTEM_PROMPT,
                        user_prompt=user_prompt,
                        image_data_urls=image_data_urls,
                    )
                else:
                    raw = self.llm_client.generate_json(system_prompt=NOTICE_PARSE_SYSTEM_PROMPT, user_prompt=user_prompt)
            else:
                raw = self.llm_client.generate_json(system_prompt=NOTICE_PARSE_SYSTEM_PROMPT, user_prompt=user_prompt)
        except Exception as exc:
            if payload.input_type == "pdf":
                logger.warning(
                    "LLM notice parser PDF-image prompt failed. Retrying with text-only payload for PDF. reason=%s",
                    self._short_error(exc),
                    extra={"function": f"{self.__class__.__name__}.parse"},
                )
                fallback_prompt = self._build_user_prompt(payload=payload, source_text=source_text)
                raw = self.llm_client.generate_json(system_prompt=NOTICE_PARSE_SYSTEM_PROMPT, user_prompt=fallback_prompt)
            else:
                raise
        return self._coerce_parse_result(payload=payload, source_text=source_text, raw=raw)

    def _extract_source_text(self, payload: NoticeSearchRequest) -> str:
        if payload.input_type == "json" and payload.body_json:
            return payload.body_json.content
        return payload.body or ""

    def _build_user_prompt(self, *, payload: NoticeSearchRequest, source_text: str) -> str:
        body_law_name = payload.body_json.law_name if payload.body_json else None
        prompt_payload: dict[str, Any] = {
            "input_type": payload.input_type,
            "title": payload.title,
            "law_name_hint": body_law_name,
        }
        if payload.input_type != "pdf":
            prompt_payload["content"] = source_text
        return json.dumps(prompt_payload, ensure_ascii=False, indent=2)

    def _short_error(self, exc: Exception, limit: int = 1200) -> str:
        message = " ".join(str(exc).split())
        return message if len(message) <= limit else f"{message[:limit]}..."

    def _coerce_parse_result(self, *, payload: NoticeSearchRequest, source_text: str, raw: dict[str, Any]) -> NoticeParseResult:
        change_types = self._coerce_change_types(raw.get("change_types"))
        analysis_mode = self._coerce_overall_analysis_mode(raw.get("analysis_mode"))
        legacy_change_type = self._coerce_change_type(raw.get("change_type"))
        article_candidates = self._coerce_candidates(raw.get("article_candidates"))
        if analysis_mode in {"DIFF", "STRUCTURE"}:
            for item in article_candidates:
                if item.analysis_mode is None:
                    item.analysis_mode = analysis_mode
        if not article_candidates and source_text:
            article_candidates = [NoticeArticleCandidate(source_text=source_text)]
        if not change_types:
            collected = [item.change_type for item in article_candidates if item.change_type]
            change_types = self._dedupe_change_types(collected)
        if not change_types:
            change_types = [legacy_change_type]
        if analysis_mode is None:
            candidate_modes = {item.analysis_mode for item in article_candidates if item.analysis_mode}
            if len(candidate_modes) == 1:
                analysis_mode = next(iter(candidate_modes))
            elif len(candidate_modes) > 1:
                analysis_mode = "MIXED"
            else:
                analysis_mode = "STRUCTURE" if "제정" in change_types else "DIFF"
        return NoticeParseResult(
            doc_type=payload.input_type,
            analysis_mode=analysis_mode,
            title=payload.title,
            law_name=self._coerce_nullable_text(raw.get("law_name")),
            change_types=change_types,
            article_candidates=article_candidates,
        )

    def _coerce_change_type(self, value: Any) -> str:
        text = normalize_text(str(value or ""))
        if text in {"일부개정", "전부개정", "제정", "폐지", "미상"}:
            return text
        alias_map = {
            "신설": "제정",
            "삭제": "폐지",
            "개정": "일부개정",
        }
        return alias_map.get(text, "미상")

    def _coerce_change_types(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return self._dedupe_change_types(self._coerce_change_type(item) for item in value)

    def _dedupe_change_types(self, values) -> list[str]:
        ordered: list[str] = []
        seen: set[str] = set()
        for raw in values:
            coerced = self._coerce_change_type(raw)
            if coerced in seen:
                continue
            seen.add(coerced)
            ordered.append(coerced)
        return ordered

    def _coerce_overall_analysis_mode(self, value: Any) -> str | None:
        text = normalize_text(str(value or "")).upper()
        if text in {"DIFF", "STRUCTURE", "MIXED"}:
            return text
        return None

    def _coerce_candidates(self, value: Any) -> list[NoticeArticleCandidate]:
        if not isinstance(value, list):
            return []
        candidates: list[NoticeArticleCandidate] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            source_text = str(item.get("source_text", ""))
            if not source_text.strip():
                continue
            candidates.append(
                NoticeArticleCandidate(
                    article_no=self._coerce_nullable_text(item.get("article_no")),
                    article_ref_text=self._coerce_nullable_text(item.get("article_ref_text")),
                    change_type=self._coerce_change_type(item.get("change_type")),
                    analysis_mode=self._coerce_candidate_mode(item.get("analysis_mode")),
                    source_text=source_text,
                )
            )
        return candidates

    def _coerce_candidate_mode(self, value: Any) -> str | None:
        text = normalize_text(str(value or "")).upper()
        if text in {"DIFF", "STRUCTURE"}:
            return text
        return None

    def _coerce_nullable_text(self, value: Any) -> str | None:
        text = normalize_text(str(value or ""))
        return text or None
