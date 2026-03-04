from __future__ import annotations

import json

from app.clients.llm_client import LlmClient, build_llm_client
from app.schemas.search import DiffHighlight, DiffSegment, NoticeArticleDiff, NumericChange
from app.services.text_normalizer import normalize_text


CHANGE_ANALYST_SYSTEM_PROMPT = """
당신은 LegisGuard-ChangeAnalyst 이다.
입법예고 문장과 현행 조문을 비교하여 변경 사실만 구조화한다.
법률 해석, 리스크 판단, 조직 영향 판단은 하지 않는다.
반드시 JSON 객체만 반환한다. 키는 아래와 같다.
- before_text: 현행 조문에서 실제로 변경되는 문장 또는 항 본문. current_text 전체를 그대로 복사하지 말고, 비교 대상이 되는 최소 범위만 추출
- after_text: 개정 후 조문 또는 개정된 항 본문. source_text를 그대로 반복하지 말고, 실제 반영 결과 문장으로 정리
- diff_summary: 짧은 한글 요약
- labels: 고정형 한글 라벨 배열. 예: ["빈도변경", "문구정정", "조문개정"]
- highlights: 배열, 각 원소는 {type, before, after}
- numeric_changes: 배열, 각 원소는 {field, before, after}
- diff_segments: 배열, 각 원소는 {op, text}
op 는 equal, delete, insert 만 허용한다.
highlight type 은 replace, insert, delete 만 허용한다.
알 수 없는 값은 빈 문자열 또는 빈 배열로 반환한다.
""".strip()


class LlmChangeAnalysisTool:
    def __init__(self, llm_client: LlmClient | None = None) -> None:
        self.llm_client = llm_client or build_llm_client()

    def analyze(self, *, current_text: str, source_text: str, base_diff: NoticeArticleDiff) -> NoticeArticleDiff:
        user_prompt = self._build_user_prompt(current_text=current_text, source_text=source_text)
        payload = self.llm_client.generate_json(system_prompt=CHANGE_ANALYST_SYSTEM_PROMPT, user_prompt=user_prompt)
        before_text = normalize_text(str(payload.get("before_text", ""))) or base_diff.before_text
        after_text = normalize_text(str(payload.get("after_text", ""))) or base_diff.after_text

        return base_diff.model_copy(
            update={
                "analysis_method": "llm",
                "before_text": before_text,
                "after_text": after_text,
                "diff_summary": normalize_text(str(payload.get("diff_summary", ""))) or None,
                "labels": self._coerce_labels(payload.get("labels")),
                "highlights": self._coerce_highlights(payload.get("highlights")),
                "numeric_changes": self._coerce_numeric_changes(payload.get("numeric_changes")),
                "diff_segments": self._coerce_diff_segments(payload.get("diff_segments")),
            }
        )

    def _build_user_prompt(self, *, current_text: str, source_text: str) -> str:
        return json.dumps(
            {
                "source_text": source_text,
                "current_text": current_text,
            },
            ensure_ascii=False,
            indent=2,
        )

    def _coerce_labels(self, value) -> list[str]:
        if not isinstance(value, list):
            return []
        return [normalize_text(str(item)) for item in value if normalize_text(str(item))]

    def _coerce_highlights(self, value) -> list[DiffHighlight]:
        if not isinstance(value, list):
            return []
        highlights: list[DiffHighlight] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", "")).strip().lower()
            if item_type not in {"replace", "insert", "delete"}:
                continue
            before = item.get("before")
            after = item.get("after")
            highlights.append(
                DiffHighlight(
                    type=item_type,
                    before=normalize_text(str(before)) if before is not None and str(before).strip() else None,
                    after=normalize_text(str(after)) if after is not None and str(after).strip() else None,
                )
            )
        return highlights

    def _coerce_numeric_changes(self, value) -> list[NumericChange]:
        if not isinstance(value, list):
            return []
        numeric_changes: list[NumericChange] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            before = normalize_text(str(item.get("before", "")))
            after = normalize_text(str(item.get("after", "")))
            field = normalize_text(str(item.get("field", ""))) or None
            if not before and not after:
                continue
            numeric_changes.append(NumericChange(field=field, before=before, after=after))
        return numeric_changes

    def _coerce_diff_segments(self, value) -> list[DiffSegment]:
        if not isinstance(value, list):
            return []
        segments: list[DiffSegment] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            op = str(item.get("op", "")).strip().lower()
            text = normalize_text(str(item.get("text", "")))
            if op not in {"equal", "delete", "insert"} or not text:
                continue
            segments.append(DiffSegment(op=op, text=text))
        return segments
