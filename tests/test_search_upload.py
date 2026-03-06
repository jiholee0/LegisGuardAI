from __future__ import annotations

import base64

import app.services.upload_payload as upload_payload
from app.services.upload_payload import build_notice_request_from_upload, detect_input_type


def test_detect_input_type_by_extension():
    assert detect_input_type("sample.pdf", None, None) == "pdf"
    assert detect_input_type("sample.json", None, None) == "json"
    assert detect_input_type("sample.txt", None, None) == "text"


def test_build_notice_request_from_text_upload():
    payload = build_notice_request_from_upload(
        filename="notice.txt",
        content_type="text/plain",
        input_type_hint=None,
        title="산업안전보건법 일부개정안",
        file_bytes="제23조 중 \"매년 1회\"를 \"6개월마다 1회\"로 한다.".encode("utf-8"),
    )
    assert payload.input_type == "text"
    assert payload.title == "산업안전보건법 일부개정안"
    assert "제23조" in (payload.body or "")


def test_build_notice_request_from_json_upload_minimal_schema():
    payload = build_notice_request_from_upload(
        filename="notice.json",
        content_type="application/json",
        input_type_hint=None,
        title="산업안전보건법 일부개정안",
        file_bytes='{"law_name":"산업안전보건법","content":"제29조의2를 다음과 같이 신설한다."}'.encode("utf-8"),
    )
    assert payload.input_type == "json"
    assert payload.body_json is not None
    assert payload.body_json.law_name == "산업안전보건법"
    assert "신설" in payload.body_json.content


def test_build_notice_request_from_pdf_upload_keeps_raw_base64(monkeypatch):
    monkeypatch.setattr(upload_payload, "extract_pdf_text", lambda _: "제23조 중 문구를 변경한다.")
    pdf_bytes = b"%PDF-1.4 sample"
    payload = build_notice_request_from_upload(
        filename="notice.pdf",
        content_type="application/pdf",
        input_type_hint=None,
        title="산업안전보건법 일부개정안",
        file_bytes=pdf_bytes,
    )

    assert payload.input_type == "pdf"
    assert payload.body == "제23조 중 문구를 변경한다."
    assert payload.raw_pdf_base64 == base64.b64encode(pdf_bytes).decode("ascii")
