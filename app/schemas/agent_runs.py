from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.search import NoticeAnalysisMode, NoticeArticleDiff, NoticeChangeType, ToolAuditItem

RunStatus = Literal["queued", "running", "completed", "failed"]
InputType = Literal["pdf", "json", "text"]


class AgentRunCreateRequest(BaseModel):
    input: dict[str, Any]
    options: dict[str, Any] = Field(default_factory=dict)
    client_request_id: str | None = None


class AgentRunInputRaw(BaseModel):
    filename: str | None = None
    mime_type: str | None = None
    content: str | None = None
    content_base64: str | None = None
    content_json: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentRunInputNormalized(BaseModel):
    doc_type: Literal["text", "json", "pdf"]
    title: str | None = None
    law_name: str | None = None
    change_types: list[NoticeChangeType] = Field(default_factory=list)


class AgentRunInput(BaseModel):
    type: InputType
    raw: AgentRunInputRaw
    normalized: AgentRunInputNormalized | None = None


class SharedArticleCandidate(BaseModel):
    article_no: str | None = None
    target_locator: str | None = None
    change_type: NoticeChangeType | None = None
    analysis_mode: Literal["DIFF", "STRUCTURE"] | None = None
    source_text: str | None = None


class AgentRunSharedContext(BaseModel):
    analysis_mode: NoticeAnalysisMode | None = None
    article_candidates: list[SharedArticleCandidate] = Field(default_factory=list)


class AgentState(BaseModel):
    agent: Literal["orchestrator", "change_analyst"]
    status: RunStatus
    started_at: datetime | None = None
    ended_at: datetime | None = None
    result: dict[str, Any] = Field(default_factory=dict)
    tool_audit: list[ToolAuditItem] = Field(default_factory=list)


class AgentRunFinalResult(BaseModel):
    schema_version: str = "v1"
    report: dict[str, Any] | None = None


class AgentRunSnapshot(BaseModel):
    run_id: str
    status: RunStatus
    created_at: datetime
    updated_at: datetime
    input: AgentRunInput
    shared_context: AgentRunSharedContext = Field(default_factory=AgentRunSharedContext)
    agents: list[AgentState]
    final_result: AgentRunFinalResult = Field(default_factory=AgentRunFinalResult)
    error: str | None = None


class AgentRunEvent(BaseModel):
    seq: int
    event: str
    timestamp: datetime
    data: dict[str, Any]


class ChangeAnalystResultPayload(BaseModel):
    article_diffs: list[NoticeArticleDiff] = Field(default_factory=list)
