from typing import Literal

from pydantic import BaseModel, Field, model_validator


class NoticeBodyJson(BaseModel):
    law_name: str | None = None
    content: str


class NoticeSearchRequest(BaseModel):
    input_type: Literal["text", "json"]
    title: str | None = None
    body: str | None = None
    body_json: NoticeBodyJson | None = None
    top_k: int = Field(default=5, ge=1, le=20)

    @model_validator(mode="after")
    def validate_payload(self) -> "NoticeSearchRequest":
        if self.input_type == "text" and not self.body:
            raise ValueError("body is required when input_type is 'text'")
        if self.input_type == "json" and not self.body_json:
            raise ValueError("body_json is required when input_type is 'json'")
        return self


class NoticeSearchMatch(BaseModel):
    law_name: str
    article_no: str
    article_title: str | None = None
    article_text: str
    score: float
    article_key: str


class NoticeSearchUnit(BaseModel):
    query_text: str
    matches: list[NoticeSearchMatch]


class NoticeSearchResponse(BaseModel):
    query_units: list[NoticeSearchUnit]


class NoticeArticleCandidate(BaseModel):
    article_no: str | None = None
    article_ref_text: str | None = None
    source_text: str


class NoticeParseResult(BaseModel):
    doc_type: Literal["text", "json"]
    title: str | None = None
    law_name: str | None = None
    change_type: Literal["일부개정", "전부개정", "제정", "폐지", "미상"]
    normalized_text: str
    article_candidates: list[NoticeArticleCandidate]


class DiffSegment(BaseModel):
    op: Literal["equal", "delete", "insert"]
    text: str


class NumericChange(BaseModel):
    field: str | None = None
    before: str
    after: str


class DiffHighlight(BaseModel):
    type: Literal["replace", "insert", "delete"]
    before: str | None = None
    after: str | None = None


class NoticeArticleDiff(BaseModel):
    article_no: str | None = None
    target_locator: str | None = None
    target_exists: bool | None = None
    fact_status: Literal["confirmed", "invalid_target", "unmatched"] = "confirmed"
    validation_message: str | None = None
    matched_law_name: str | None = None
    matched_article_no: str | None = None
    matched_article_key: str | None = None
    current_text: str | None = None
    before_text: str | None = None
    after_text: str | None = None
    diff_summary: str | None = None
    labels: list[str] = Field(default_factory=list)
    match_score: float | None = None
    match_method: Literal["exact_article_no", "vector_search", "unmatched"]
    analysis_method: Literal["rule_based", "llm"]
    diff_segments: list[DiffSegment] = Field(default_factory=list)
    highlights: list[DiffHighlight] = Field(default_factory=list)
    numeric_changes: list[NumericChange] = Field(default_factory=list)
    source_text: str


class ToolAuditItem(BaseModel):
    tool_name: str
    status: Literal["success", "skipped", "error"]
    input_summary: str | None = None
    output_summary: str | None = None


class NoticeDiffResponse(BaseModel):
    agent: Literal["change_analyst"] = "change_analyst"
    parsed_notice: NoticeParseResult
    article_diffs: list[NoticeArticleDiff]
    tool_audit: list[ToolAuditItem] = Field(default_factory=list)
