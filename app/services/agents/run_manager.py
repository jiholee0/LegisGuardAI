from __future__ import annotations

import base64
import json
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

from app.schemas.agent_runs import (
    AgentRunCreateRequest,
    AgentRunEvent,
    AgentRunFinalResult,
    AgentRunInput,
    AgentRunInputNormalized,
    AgentRunInputRaw,
    AgentRunSharedContext,
    AgentRunSnapshot,
    AgentState,
    SharedArticleCandidate,
)
from app.schemas.search import NoticeBodyJson, NoticeSearchRequest
from app.services.agents.change_analyst import ChangeAnalystService
from app.services.agents.orchestrator import NoticeOrchestratorService
from app.services.agents.tools.llm_notice_parser import LlmNoticeParserTool
from app.services.upload_payload import build_notice_request_from_upload


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class AgentRunManager:
    def __init__(
        self,
        parser: LlmNoticeParserTool | None = None,
        change_analyst_service: ChangeAnalystService | None = None,
    ) -> None:
        self._orchestrator = NoticeOrchestratorService(
            parser=parser or LlmNoticeParserTool(),
            change_analyst_service=change_analyst_service or ChangeAnalystService(),
        )
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._runs: dict[str, AgentRunSnapshot] = {}
        self._events: dict[str, list[AgentRunEvent]] = {}

    def create_run(self, request: AgentRunCreateRequest) -> AgentRunSnapshot:
        parsed_input = self._parse_input(input_payload=request.input)
        run_id = str(uuid.uuid4())
        now = _now()
        snapshot = AgentRunSnapshot(
            run_id=run_id,
            status="queued",
            created_at=now,
            updated_at=now,
            input=parsed_input,
            shared_context=AgentRunSharedContext(),
            agents=[
                AgentState(agent="orchestrator", status="queued"),
                AgentState(agent="change_analyst", status="queued"),
            ],
            final_result=AgentRunFinalResult(),
            error=None,
        )
        with self._condition:
            self._runs[run_id] = snapshot
            self._events[run_id] = []
            self._append_event_unlocked(run_id, "run.queued", {"run_id": run_id, "status": "queued"})

        thread = threading.Thread(target=self._execute_run, args=(run_id,), daemon=True)
        thread.start()
        return self.get_run(run_id)

    def get_run(self, run_id: str) -> AgentRunSnapshot:
        with self._lock:
            snapshot = self._runs.get(run_id)
            if snapshot is None:
                raise HTTPException(status_code=404, detail="run_id not found")
            return snapshot.model_copy(deep=True)

    def get_events(
        self,
        run_id: str,
        *,
        since_seq: int = 0,
        follow: bool = True,
        wait_seconds: int = 15,
    ) -> list[AgentRunEvent]:
        with self._condition:
            if run_id not in self._runs:
                raise HTTPException(status_code=404, detail="run_id not found")

            def unread() -> list[AgentRunEvent]:
                return [event for event in self._events[run_id] if event.seq > since_seq]

            pending = unread()
            if pending:
                return [event.model_copy(deep=True) for event in pending]
            if not follow:
                return []

            completed = self._runs[run_id].status in {"completed", "failed"}
            if completed:
                return []

            self._condition.wait(timeout=wait_seconds)
            return [event.model_copy(deep=True) for event in unread()]

    def _execute_run(self, run_id: str) -> None:
        try:
            self._set_run_status(run_id, "running")
            snapshot = self.get_run(run_id)
            request_payload = self._to_notice_request(snapshot.input)
            self._orchestrator.execute_agent_pipeline(
                request_payload,
                on_agent_status=lambda agent, status: self._set_agent_status(run_id, agent, status),
                on_parsed_notice=lambda parsed_notice: self._update_snapshot_for_parsed_notice(run_id, parsed_notice),
                on_agent_result=lambda agent, result, audit: self._update_agent_result(
                    run_id,
                    agent,
                    result=result,
                    tool_audit=audit,
                ),
                on_final_result=lambda report: self._set_final_result(run_id, report=report),
            )
            self._set_run_status(run_id, "completed")
        except Exception as exc:
            self._set_run_failed(run_id, str(exc))
            self._append_event(run_id, "run.error", {"run_id": run_id, "message": str(exc)})

    def _parse_input(self, *, input_payload: dict[str, Any]) -> AgentRunInput:
        input_type = input_payload.get("type")
        raw_payload = input_payload.get("raw") or {}
        if input_type not in {"pdf", "json", "text"}:
            raise HTTPException(status_code=400, detail="input.type must be one of: pdf, json, text")
        raw = AgentRunInputRaw.model_validate(raw_payload)
        return AgentRunInput(type=input_type, raw=raw, normalized=None)

    def _to_notice_request(self, run_input: AgentRunInput) -> NoticeSearchRequest:
        raw = run_input.raw
        title = raw.metadata.get("title") if raw.metadata else None
        if run_input.type == "text":
            if not raw.content:
                raise HTTPException(status_code=400, detail="input.raw.content is required for text")
            return NoticeSearchRequest(input_type="text", title=title, body=raw.content)

        if run_input.type == "json":
            if raw.content_json is not None:
                content_obj: Any = raw.content_json
            elif raw.content:
                try:
                    content_obj = json.loads(raw.content)
                except Exception as exc:
                    raise HTTPException(status_code=400, detail="input.raw.content is not valid JSON") from exc
            else:
                raise HTTPException(status_code=400, detail="json input requires input.raw.content or input.raw.content_json")

            if isinstance(content_obj, dict) and content_obj.get("input_type") in {"text", "json", "pdf"}:
                return NoticeSearchRequest.model_validate(content_obj)

            if isinstance(content_obj, dict) and "content" in content_obj:
                return NoticeSearchRequest(
                    input_type="json",
                    title=title,
                    body_json=NoticeBodyJson(
                        law_name=content_obj.get("law_name"),
                        content=str(content_obj.get("content") or ""),
                    ),
                )

            raise HTTPException(status_code=400, detail="json input must include either NoticeSearchRequest or {law_name?, content}")

        if not raw.content_base64:
            raise HTTPException(status_code=400, detail="pdf input requires input.raw.content_base64")
        try:
            file_bytes = base64.b64decode(raw.content_base64)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="input.raw.content_base64 is invalid") from exc

        payload = build_notice_request_from_upload(
            filename=raw.filename,
            content_type=raw.mime_type,
            input_type_hint="pdf",
            title=title,
            file_bytes=file_bytes,
        )
        return payload

    def _set_run_status(self, run_id: str, status: str) -> None:
        with self._condition:
            run = self._require_run_unlocked(run_id)
            run.status = status
            run.updated_at = _now()
            self._append_event_unlocked(run_id, "run.status", {"run_id": run_id, "status": status})
            if status in {"completed", "failed"}:
                self._append_event_unlocked(run_id, f"run.{status}", {"run_id": run_id, "status": status})

    def _set_agent_status(self, run_id: str, agent_name: str, status: str) -> None:
        with self._condition:
            run = self._require_run_unlocked(run_id)
            now = _now()
            agent = self._require_agent(run, agent_name)
            if status == "running" and agent.started_at is None:
                agent.started_at = now
            if status in {"completed", "failed"}:
                agent.ended_at = now
            agent.status = status
            run.updated_at = now
            self._append_event_unlocked(
                run_id,
                "agent.status",
                {"run_id": run_id, "agent": agent_name, "status": status},
            )

    def _update_snapshot_for_parsed_notice(self, run_id: str, parsed_notice) -> None:
        with self._condition:
            run = self._require_run_unlocked(run_id)
            run.input.normalized = AgentRunInputNormalized(
                doc_type=parsed_notice.doc_type,
                title=parsed_notice.title,
                law_name=parsed_notice.law_name,
                change_types=parsed_notice.change_types,
            )
            run.shared_context = AgentRunSharedContext(
                analysis_mode=parsed_notice.analysis_mode,
                article_candidates=[
                    SharedArticleCandidate(
                        article_no=candidate.article_no,
                        target_locator=candidate.article_ref_text,
                        change_type=candidate.change_type,
                        analysis_mode=candidate.analysis_mode,
                        source_text=candidate.source_text,
                    )
                    for candidate in parsed_notice.article_candidates
                ],
            )
            run.updated_at = _now()
            self._append_event_unlocked(
                run_id,
                "orchestrator.normalized",
                {
                    "run_id": run_id,
                    "analysis_mode": parsed_notice.analysis_mode,
                    "candidate_count": len(parsed_notice.article_candidates),
                },
            )

    def _update_agent_result(self, run_id: str, agent_name: str, *, result: dict[str, Any], tool_audit) -> None:
        with self._condition:
            run = self._require_run_unlocked(run_id)
            agent = self._require_agent(run, agent_name)
            agent.result = result
            agent.tool_audit = list(tool_audit)
            run.updated_at = _now()
            self._append_event_unlocked(
                run_id,
                "agent.result",
                {"run_id": run_id, "agent": agent_name, "tool_audit_count": len(agent.tool_audit)},
            )

    def _set_final_result(self, run_id: str, *, report: dict[str, Any]) -> None:
        with self._condition:
            run = self._require_run_unlocked(run_id)
            run.final_result = AgentRunFinalResult(schema_version="v1", report=report)
            run.updated_at = _now()
            self._append_event_unlocked(run_id, "final.result", {"run_id": run_id, "schema_version": "v1"})

    def _set_run_failed(self, run_id: str, message: str) -> None:
        with self._condition:
            run = self._require_run_unlocked(run_id)
            run.status = "failed"
            run.error = message
            run.updated_at = _now()
            for agent in run.agents:
                if agent.status == "running":
                    agent.status = "failed"
                    agent.ended_at = _now()
            self._append_event_unlocked(run_id, "run.status", {"run_id": run_id, "status": "failed"})
            self._append_event_unlocked(run_id, "run.failed", {"run_id": run_id, "message": message})

    def _append_event(self, run_id: str, event_name: str, data: dict[str, Any]) -> None:
        with self._condition:
            self._append_event_unlocked(run_id, event_name, data)

    def _append_event_unlocked(self, run_id: str, event_name: str, data: dict[str, Any]) -> None:
        events = self._events[run_id]
        event = AgentRunEvent(
            seq=len(events) + 1,
            event=event_name,
            timestamp=_now(),
            data=data,
        )
        events.append(event)
        self._condition.notify_all()

    def _require_run_unlocked(self, run_id: str) -> AgentRunSnapshot:
        run = self._runs.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run_id not found")
        return run

    def _require_agent(self, run: AgentRunSnapshot, name: str) -> AgentState:
        for agent in run.agents:
            if agent.agent == name:
                return agent
        raise HTTPException(status_code=500, detail=f"agent state not found: {name}")
