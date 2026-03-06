from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse

from app.schemas.agent_runs import AgentRunCreateRequest, AgentRunSnapshot
from app.services.agents.run_manager import AgentRunManager
from app.services.upload_payload import detect_input_type

router = APIRouter(prefix="/agent", tags=["agent-runs"])
logger = logging.getLogger(__name__)

_run_manager = AgentRunManager()


def get_agent_run_manager() -> AgentRunManager:
    return _run_manager


def _snapshot_without_base64(snapshot: AgentRunSnapshot) -> dict[str, Any]:
    return snapshot.model_dump(exclude={"input": {"raw": {"content_base64"}}})


@router.post("/runs", response_model=AgentRunSnapshot, response_model_exclude_none=True)
def create_agent_run(
    payload: AgentRunCreateRequest,
    manager: AgentRunManager = Depends(get_agent_run_manager),
) -> dict[str, Any]:
    return _snapshot_without_base64(manager.create_run(payload))


try:
    import multipart  # type: ignore # noqa: F401
    from fastapi import File, HTTPException, UploadFile

    @router.post("/runs/upload", response_model=AgentRunSnapshot, response_model_exclude_none=True)
    async def create_agent_run_upload(
        file: UploadFile = File(...),
        manager: AgentRunManager = Depends(get_agent_run_manager),
    ) -> dict[str, Any]:
        file_bytes = await file.read()
        if not file_bytes:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        resolved_type = detect_input_type(file.filename, file.content_type, None)
        filename = file.filename or ""
        auto_title = filename.rsplit(".", 1)[0] if "." in filename else filename
        metadata = {"title": auto_title} if auto_title else {}

        if resolved_type == "pdf":
            payload = AgentRunCreateRequest(
                input={
                    "type": "pdf",
                    "raw": {
                        "filename": file.filename,
                        "mime_type": file.content_type,
                        "content_base64": base64.b64encode(file_bytes).decode("ascii"),
                        "metadata": metadata,
                    },
                }
            )
            return _snapshot_without_base64(manager.create_run(payload))

        try:
            content_text = file_bytes.decode("utf-8")
        except Exception as exc:
            raise HTTPException(status_code=400, detail="Text/JSON file must be UTF-8 encoded.") from exc

        payload = AgentRunCreateRequest(
            input={
                "type": resolved_type,
                "raw": {
                    "filename": file.filename,
                    "mime_type": file.content_type,
                    "content": content_text,
                    "metadata": metadata,
                },
            }
        )
        return _snapshot_without_base64(manager.create_run(payload))
except Exception:
    logger.warning("Agent run upload route disabled because python-multipart is not installed.")


@router.get("/runs/{run_id}", response_model=AgentRunSnapshot, response_model_exclude_none=True)
def get_agent_run(
    run_id: str,
    manager: AgentRunManager = Depends(get_agent_run_manager),
) -> dict[str, Any]:
    return _snapshot_without_base64(manager.get_run(run_id))


@router.get("/runs/{run_id}/events")
def stream_agent_run_events(
    run_id: str,
    since_seq: int = 0,
    follow: bool = True,
    manager: AgentRunManager = Depends(get_agent_run_manager),
) -> StreamingResponse:
    def generate():
        cursor = since_seq
        while True:
            events = manager.get_events(run_id, since_seq=cursor, follow=follow)
            if not events:
                if not follow:
                    break
                yield ": keep-alive\n\n"
                time.sleep(0.1)
                continue

            for event in events:
                payload = json.dumps(jsonable_encoder(event.data), ensure_ascii=False)
                yield f"id: {event.seq}\n"
                yield f"event: {event.event}\n"
                yield f"data: {payload}\n\n"
                cursor = event.seq

            if follow:
                snapshot = manager.get_run(run_id)
                if snapshot.status in {"completed", "failed"}:
                    break

    return StreamingResponse(generate(), media_type="text/event-stream")
