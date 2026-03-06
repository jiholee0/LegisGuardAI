from __future__ import annotations

import base64
import json
from io import BytesIO

from fastapi import HTTPException

from app.schemas.search import NoticeBodyJson, NoticeSearchRequest


def detect_input_type(filename: str | None, content_type: str | None, input_type_hint: str | None) -> str:
    if input_type_hint in {"text", "json", "pdf"}:
        return input_type_hint
    lowered_name = (filename or "").lower()
    if lowered_name.endswith(".pdf") or content_type == "application/pdf":
        return "pdf"
    if lowered_name.endswith(".json") or content_type == "application/json":
        return "json"
    return "text"


def extract_pdf_text(file_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail="PDF parsing dependency is missing. Install 'pypdf' to enable PDF upload parsing.",
        ) from exc
    reader = PdfReader(BytesIO(file_bytes))
    page_texts: list[str] = []
    for page in reader.pages:
        page_text = page.extract_text() or ""
        if page_text.strip():
            page_texts.append(page_text)
    merged = "\n".join(page_texts).strip()
    if not merged:
        raise HTTPException(status_code=400, detail="PDF text extraction failed or document contains no extractable text.")
    return merged


def build_notice_request_from_upload(
    *,
    filename: str | None,
    content_type: str | None,
    input_type_hint: str | None,
    title: str | None,
    file_bytes: bytes,
) -> NoticeSearchRequest:
    input_type = detect_input_type(filename, content_type, input_type_hint)
    if input_type == "pdf":
        extracted_text = extract_pdf_text(file_bytes)
        return NoticeSearchRequest(
            input_type="pdf",
            title=title,
            body=extracted_text,
            raw_pdf_base64=base64.b64encode(file_bytes).decode("ascii"),
        )
    if input_type == "json":
        try:
            payload = json.loads(file_bytes.decode("utf-8"))
        except Exception as exc:
            raise HTTPException(status_code=400, detail="JSON file is not valid UTF-8 or has invalid JSON format.") from exc
        if isinstance(payload, dict) and payload.get("input_type") in {"text", "json", "pdf"}:
            try:
                return NoticeSearchRequest.model_validate(payload)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"JSON request schema is invalid: {exc}") from exc
        if isinstance(payload, dict) and "content" in payload:
            return NoticeSearchRequest(
                input_type="json",
                title=title,
                body_json=NoticeBodyJson(
                    law_name=payload.get("law_name"),
                    content=str(payload.get("content") or ""),
                ),
            )
        raise HTTPException(
            status_code=400,
            detail="JSON file must contain either NoticeSearchRequest schema or {law_name?, content}.",
        )
    try:
        text = file_bytes.decode("utf-8")
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Text file must be UTF-8 encoded.") from exc
    return NoticeSearchRequest(input_type="text", title=title, body=text)
