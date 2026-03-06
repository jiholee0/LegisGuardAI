from __future__ import annotations

from app.schemas.search import NoticeSearchRequest
from app.services.agents.tools.llm_notice_parser import LlmNoticeParserTool


class StubPdfImageConverter:
    def __init__(self) -> None:
        self.called = False

    def convert(self, *, pdf_base64: str) -> list[str]:
        self.called = True
        return ["data:image/jpeg;base64,abc123"]


class StubImageLlmClient:
    def __init__(self) -> None:
        self.called_with_images = False

    def generate_json_with_images(self, *, system_prompt: str, user_prompt: str, image_data_urls: list[str]) -> dict:
        self.called_with_images = True
        return {
            "law_name": "산업안전보건법",
            "change_types": ["일부개정"],
            "analysis_mode": "DIFF",
            "article_candidates": [
                {
                    "article_no": "제26조",
                    "article_ref_text": "제26조",
                    "change_type": "일부개정",
                    "analysis_mode": "DIFF",
                    "source_text": "제26조제1항 전단 중...",
                }
            ],
        }

    def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        raise AssertionError("PDF path should use image multimodal first.")


def test_llm_notice_parser_uses_pdf_image_converter_for_pdf_input():
    converter = StubPdfImageConverter()
    llm_client = StubImageLlmClient()
    parser = LlmNoticeParserTool(llm_client=llm_client, pdf_image_converter=converter)

    parsed = parser.parse(
        payload=NoticeSearchRequest(
            input_type="pdf",
            title="산업안전보건법 시행규칙 일부개정령안",
            body="추출 텍스트",
            raw_pdf_base64="JVBERi0xLjQKJQ==",
        )
    )

    assert converter.called is True
    assert llm_client.called_with_images is True
    assert parsed.doc_type == "pdf"
    assert parsed.article_candidates[0].article_no == "제26조"


class StubRawSourceLlmClient:
    def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        return {
            "law_name": "산업안전보건법",
            "change_types": ["일부개정"],
            "analysis_mode": "DIFF",
            "article_candidates": [
                {
                    "article_no": "제31조",
                    "article_ref_text": "제31조 제2항",
                    "change_type": "일부개정",
                    "analysis_mode": "DIFF",
                    "source_text": " 제31조 제2항\n  개인: 사업자등록증  →  사업자등록증명 ",
                }
            ],
        }


def test_llm_notice_parser_preserves_candidate_source_text_without_normalization():
    parser = LlmNoticeParserTool(llm_client=StubRawSourceLlmClient())
    parsed = parser.parse(
        payload=NoticeSearchRequest(
            input_type="text",
            title="산업안전보건법 시행규칙 일부개정령안",
            body="dummy",
        )
    )

    assert parsed.article_candidates[0].source_text == " 제31조 제2항\n  개인: 사업자등록증  →  사업자등록증명 "
