from __future__ import annotations

from contextlib import contextmanager
from datetime import date
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings
from app.db.models import Article, Base, Law
from app.schemas.search import NoticeBodyJson, NoticeSearchRequest
from app.services.change_analyst import ChangeAnalystService
from app.services.embedding_index import EmbeddingIndexService
from app.services.llm_change_analysis import LlmChangeAnalysisTool
from app.services.notice_parser import NoticeParserService


def build_session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'notice_diff.db'}", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

    @contextmanager
    def factory():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    return factory


def seed_articles(session_factory):
    with session_factory() as session:
        law = Law(
            law_code="LAW001",
            law_name="산업안전보건법",
            law_type="LAW",
            source="MOLEG_OPEN_API",
            effective_date=date(2024, 2, 1),
        )
        session.add(law)
        session.flush()
        session.add(
            Article(
                law_id=law.id,
                article_key="산업안전보건법:제23조",
                article_no="제23조",
                article_title="안전조치",
                article_text="사업주는 매년 1회 이상 점검하여야 한다.",
                normalized_text="사업주는 매년 1회 이상 점검하여야 한다.",
                article_order=1,
                paragraph_json=None,
                effective_date=date(2024, 2, 1),
                hash="hash1",
            )
        )
        session.commit()


def test_notice_parser_extracts_change_metadata():
    parser = NoticeParserService()

    parsed = parser.parse(
        NoticeSearchRequest(
            input_type="json",
            title="산업안전보건법 일부개정안",
            body_json=NoticeBodyJson(
                law_name="산업안전보건법",
                content='제23조 중 "매년 1회 이상"을 "6개월마다 1회 이상"으로 한다.',
            ),
        )
    )

    assert parsed.law_name == "산업안전보건법"
    assert parsed.change_type == "일부개정"
    assert parsed.article_candidates[0].article_no == "제23조"
    assert parsed.article_candidates[0].source_text == '제23조 중 "매년 1회 이상"을 "6개월마다 1회 이상"으로 한다.'


def test_notice_parser_extracts_single_candidate_for_new_article():
    parser = NoticeParserService()

    parsed = parser.parse(
        NoticeSearchRequest(
            input_type="json",
            title="산업안전보건법 일부개정안",
            body_json=NoticeBodyJson(
                law_name="산업안전보건법",
                content=(
                    "제29조의2를 다음과 같이 신설한다. "
                    "제29조의2(안전보건교육 결과의 기록 및 보존) "
                    "사업주는 제29조에 따른 안전보건교육을 실시한 경우 교육 일시, 참석자 및 교육 내용을 기록하여 3년간 보존하여야 한다."
                ),
            ),
        )
    )

    assert parsed.change_type == "제정"
    assert len(parsed.article_candidates) == 1
    assert parsed.article_candidates[0].article_no == "제29조의2"
    assert parsed.article_candidates[0].source_text.startswith("제29조의2(안전보건교육 결과의 기록 및 보존)")


def test_notice_diff_returns_exact_match_and_numeric_change(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_articles(session_factory)
    settings = get_settings()
    settings.chroma_persist_dir = str(tmp_path / "chroma_diff")
    settings.chroma_collection_name = "notice_diff_articles"

    EmbeddingIndexService(session_factory=session_factory).reindex(recreate=True)
    service = ChangeAnalystService(session_factory=session_factory)

    response = service.analyze_notice(
        NoticeSearchRequest(
            input_type="text",
            title="산업안전보건법 일부개정안",
            body='제23조 중 "매년 1회 이상 점검하여야 한다."을 "6개월마다 1회 이상 점검하여야 한다."으로 한다.',
        )
    )

    assert response.parsed_notice.change_type == "일부개정"
    assert len(response.article_diffs) == 1
    assert response.tool_audit[0].tool_name == "parse_notice_structure"
    assert response.tool_audit[0].status == "success"

    article_diff = response.article_diffs[0]
    assert article_diff.match_method == "exact_article_no"
    assert article_diff.analysis_method == "rule_based"
    assert article_diff.matched_article_key == "산업안전보건법:제23조"
    assert article_diff.before_text == "사업주는 매년 1회 이상 점검하여야 한다."
    assert article_diff.after_text == "6개월마다 1회 이상 점검하여야 한다."
    assert article_diff.numeric_changes[0].before == "1회"
    assert article_diff.numeric_changes[0].after == "6개월"
    assert any(segment.op == "insert" and "6개월" in segment.text for segment in article_diff.diff_segments)
    assert any(item.tool_name == "lawdb_get_article" and item.status == "success" for item in response.tool_audit)
    assert any(item.tool_name == "vector_search_law" and item.status == "skipped" for item in response.tool_audit)
    assert any(item.tool_name == "diff_generate_article" and item.status == "success" for item in response.tool_audit)
    assert any(item.tool_name == "classify_change_labels" and item.status == "skipped" for item in response.tool_audit)


def test_notice_diff_allows_empty_after_text_for_delete_case(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_articles(session_factory)
    settings = get_settings()
    settings.chroma_persist_dir = str(tmp_path / "chroma_delete_diff")
    settings.chroma_collection_name = "notice_delete_diff_articles"

    EmbeddingIndexService(session_factory=session_factory).reindex(recreate=True)
    service = ChangeAnalystService(session_factory=session_factory)

    response = service.analyze_notice(
        NoticeSearchRequest(
            input_type="text",
            title="산업안전보건법 일부개정안",
            body="제23조를 삭제한다.",
        )
    )

    article_diff = response.article_diffs[0]
    assert article_diff.target_locator is None
    assert article_diff.target_exists is True
    assert article_diff.fact_status == "confirmed"
    assert article_diff.before_text == "사업주는 매년 1회 이상 점검하여야 한다."
    assert article_diff.after_text is None
    assert all(segment.op != "insert" for segment in article_diff.diff_segments)


def test_notice_diff_marks_missing_delete_target_as_invalid(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_articles(session_factory)
    settings = get_settings()
    settings.chroma_persist_dir = str(tmp_path / "chroma_invalid_delete")
    settings.chroma_collection_name = "notice_invalid_delete_articles"

    EmbeddingIndexService(session_factory=session_factory).reindex(recreate=True)
    service = ChangeAnalystService(session_factory=session_factory)

    response = service.analyze_notice(
        NoticeSearchRequest(
            input_type="text",
            title="산업안전보건법 일부개정안",
            body="제23조 제3항을 삭제한다.",
        )
    )

    article_diff = response.article_diffs[0]
    assert article_diff.target_locator == "제23조 제3항"
    assert article_diff.target_exists is False
    assert article_diff.fact_status == "invalid_target"
    assert article_diff.before_text is None
    assert article_diff.after_text is None
    assert article_diff.labels == ["대상미존재"]
    assert "찾지 못함" in (article_diff.validation_message or "")
    assert any(item.tool_name == "validate_target_locator" and item.status == "success" for item in response.tool_audit)
    assert any(
        item.tool_name == "classify_change_labels" and item.status == "skipped" and item.output_summary == "invalid target locator"
        for item in response.tool_audit
    )


def test_notice_diff_marks_missing_amend_target_as_invalid(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_articles(session_factory)
    settings = get_settings()
    settings.chroma_persist_dir = str(tmp_path / "chroma_invalid_amend")
    settings.chroma_collection_name = "notice_invalid_amend_articles"

    EmbeddingIndexService(session_factory=session_factory).reindex(recreate=True)
    service = ChangeAnalystService(session_factory=session_factory)

    response = service.analyze_notice(
        NoticeSearchRequest(
            input_type="text",
            title="산업안전보건법 일부개정안",
            body='제23조 제3항 "매년 1회 이상 점검하여야 한다."을 "분기마다 1회 이상 점검하여야 한다."으로 한다.',
        )
    )

    article_diff = response.article_diffs[0]
    assert article_diff.target_locator == "제23조 제3항"
    assert article_diff.target_exists is False
    assert article_diff.fact_status == "invalid_target"
    assert article_diff.before_text is None
    assert article_diff.after_text is None
    assert article_diff.labels == ["대상미존재"]
    assert article_diff.diff_segments == []


def test_notice_diff_recognizes_existing_item_locator(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_articles(session_factory)
    settings = get_settings()
    settings.chroma_persist_dir = str(tmp_path / "chroma_existing_item")
    settings.chroma_collection_name = "notice_existing_item_articles"

    with session_factory() as session:
        law = Law(
            law_code="LAW002",
            law_name="산업안전보건법 시행령",
            law_type="DECREE",
            source="MOLEG_OPEN_API",
            effective_date=date(2024, 2, 1),
        )
        session.add(law)
        session.flush()
        session.add(
            Article(
                law_id=law.id,
                article_key="산업안전보건법 시행령:제8조의2",
                article_no="제8조의2",
                article_title="협조 요청 대상 정보 또는 자료",
                article_text=(
                    "제8조의2(협조 요청 대상 정보 또는 자료) 법 제8조제5항제3호에서 "
                    "\"대통령령으로 정하는 정보 또는 자료\"란 다음 각 호의 어느 하나에 해당하는 정보를 말한다.\n\n"
                    "1. 「전기사업법」 제16조제1항에 따른 기본공급약관에서 정하는 사업장별 계약전력 정보\n"
                    "2. 「화학물질관리법」 제9조제1항에 따른 화학물질확인 정보"
                ),
                normalized_text=(
                    "법 제8조제5항제3호에서 대통령령으로 정하는 정보 또는 자료란 다음 각 호의 어느 하나에 해당하는 정보를 말한다. "
                    "1. 전기사업법 제16조제1항에 따른 기본공급약관에서 정하는 사업장별 계약전력 정보 "
                    "2. 화학물질관리법 제9조제1항에 따른 화학물질확인 정보"
                ),
                article_order=1,
                paragraph_json=None,
                effective_date=date(2024, 2, 1),
                hash="hash2",
            )
        )
        session.commit()

    EmbeddingIndexService(session_factory=session_factory).reindex(recreate=True)
    service = ChangeAnalystService(session_factory=session_factory)

    response = service.analyze_notice(
        NoticeSearchRequest(
            input_type="text",
            title="산업안전보건법 시행령 일부개정령안",
            body="제8조의2 제1호의 사업장별 계약전력 정보에 최근 3년간 변동 이력을 포함하도록 한다.",
        )
    )

    article_diff = response.article_diffs[0]
    assert article_diff.target_locator == "제8조의2 제1호"
    assert article_diff.target_exists is True
    assert article_diff.fact_status == "confirmed"
    assert article_diff.match_method == "exact_article_no"


def test_notice_diff_treats_new_article_as_insert_without_fallback_matching(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_articles(session_factory)
    settings = get_settings()
    settings.chroma_persist_dir = str(tmp_path / "chroma_new_article")
    settings.chroma_collection_name = "notice_new_article_articles"

    EmbeddingIndexService(session_factory=session_factory).reindex(recreate=True)
    service = ChangeAnalystService(
        session_factory=session_factory,
        llm_change_tool=LlmChangeAnalysisTool(llm_client=FakeLlmClientForInsert()),
    )

    response = service.analyze_notice(
        NoticeSearchRequest(
            input_type="json",
            title="산업안전보건법 일부개정안",
            body_json=NoticeBodyJson(
                law_name="산업안전보건법",
                content=(
                    "제29조의2를 다음과 같이 신설한다. "
                    "제29조의2(안전보건교육 결과의 기록 및 보존) "
                    "사업주는 제29조에 따른 안전보건교육을 실시한 경우 교육 일시, 참석자 및 교육 내용을 기록하여 3년간 보존하여야 한다."
                ),
            ),
        )
    )

    assert len(response.article_diffs) == 1
    article_diff = response.article_diffs[0]
    assert article_diff.article_no == "제29조의2"
    assert article_diff.fact_status == "confirmed"
    assert article_diff.match_method == "unmatched"
    assert article_diff.matched_article_key is None
    assert article_diff.before_text is None
    assert article_diff.analysis_method == "llm"
    assert article_diff.after_text and article_diff.after_text.startswith("제29조의2(안전보건교육 결과의 기록 및 보존)")
    assert any(
        item.tool_name == "vector_search_law"
        and item.status == "skipped"
        and item.output_summary == "new article insertion skips fallback matching"
        for item in response.tool_audit
    )


def test_vector_search_rejects_irrelevant_low_similarity_match(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_articles(session_factory)
    settings = get_settings()
    settings.chroma_persist_dir = str(tmp_path / "chroma_irrelevant_vector")
    settings.chroma_collection_name = "notice_irrelevant_vector_articles"

    EmbeddingIndexService(session_factory=session_factory).reindex(recreate=True)
    service = ChangeAnalystService(session_factory=session_factory)

    response = service.analyze_notice(
        NoticeSearchRequest(
            input_type="text",
            title="산업안전보건법 시행규칙 일부개정령안",
            body="고객응대업무 매뉴얼 마련 의무 조항 중 대처방법에 정기 점검 절차를 포함하도록 하고, 건강장해 예방 관련 교육은 반기마다 실시하도록 한다.",
        )
    )

    article_diff = response.article_diffs[0]
    assert article_diff.match_method == "unmatched"
    assert article_diff.matched_article_key is None


class FakeLlmClient:
    def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        return {
            "before_text": "사업주는 매년 1회 이상 점검하여야 한다.",
            "after_text": "사업주는 6개월마다 1회 이상 점검하여야 한다.",
            "diff_summary": "점검 주기를 매년에서 반기마다로 변경",
            "labels": ["빈도변경", "조문개정"],
            "highlights": [
                {"type": "replace", "before": "매년 1회 이상", "after": "6개월마다 1회 이상"}
            ],
            "numeric_changes": [
                {"field": "period", "before": "1년", "after": "6개월"}
            ],
            "diff_segments": [
                {"op": "delete", "text": "매년 1회 이상"},
                {"op": "insert", "text": "6개월마다 1회 이상"},
                {"op": "equal", "text": "점검하여야 한다 ."},
            ],
        }


class FakeLlmClientForInsert:
    def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        return {
            "before_text": "",
            "after_text": "제29조의2(안전보건교육 결과의 기록 및 보존) 사업주는 제29조에 따른 안전보건교육을 실시한 경우 교육 일시, 참석자 및 교육 내용을 기록하여 3년간 보존하여야 한다.",
            "diff_summary": "제29조의2 신설",
            "labels": ["조문신설", "보존기간명시"],
            "highlights": [
                {
                    "type": "insert",
                    "before": None,
                    "after": "제29조의2(안전보건교육 결과의 기록 및 보존) 사업주는 제29조에 따른 안전보건교육을 실시한 경우 교육 일시, 참석자 및 교육 내용을 기록하여 3년간 보존하여야 한다.",
                }
            ],
            "numeric_changes": [
                {"field": "보존기간", "before": "", "after": "3년"}
            ],
            "diff_segments": [
                {
                    "op": "insert",
                    "text": "제29조의2(안전보건교육 결과의 기록 및 보존) 사업주는 제29조에 따른 안전보건교육을 실시한 경우 교육 일시, 참석자 및 교육 내용을 기록하여 3년간 보존하여야 한다.",
                }
            ],
        }


class TimeoutLlmClient:
    def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        raise RuntimeError("LLM request timed out while calling https://example.test")


def test_notice_diff_can_use_llm_change_tool(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_articles(session_factory)
    settings = get_settings()
    settings.chroma_persist_dir = str(tmp_path / "chroma_llm_diff")
    settings.chroma_collection_name = "notice_llm_diff_articles"

    EmbeddingIndexService(session_factory=session_factory).reindex(recreate=True)
    service = ChangeAnalystService(
        session_factory=session_factory,
        llm_change_tool=LlmChangeAnalysisTool(llm_client=FakeLlmClient()),
    )

    response = service.analyze_notice(
        NoticeSearchRequest(
            input_type="text",
            title="산업안전보건법 일부개정안",
            body='제23조 중 "매년 1회 이상 점검하여야 한다."을 "6개월마다 1회 이상 점검하여야 한다."으로 한다.',
        )
    )

    article_diff = response.article_diffs[0]
    assert article_diff.analysis_method == "llm"
    assert article_diff.before_text == "사업주는 매년 1회 이상 점검하여야 한다."
    assert article_diff.after_text == "사업주는 6개월마다 1회 이상 점검하여야 한다."
    assert article_diff.diff_summary == "점검 주기를 매년에서 반기마다로 변경"
    assert article_diff.labels == ["빈도변경", "조문개정"]
    assert article_diff.highlights[0].type == "replace"
    assert article_diff.numeric_changes[0].field == "period"
    assert article_diff.numeric_changes[0].before == "1년"
    assert article_diff.numeric_changes[0].after == "6개월"
    assert any(item.tool_name == "classify_change_labels" and item.status == "success" for item in response.tool_audit)


def test_notice_diff_falls_back_to_rule_based_when_llm_fails(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_articles(session_factory)
    settings = get_settings()
    settings.chroma_persist_dir = str(tmp_path / "chroma_llm_timeout")
    settings.chroma_collection_name = "notice_llm_timeout_articles"

    EmbeddingIndexService(session_factory=session_factory).reindex(recreate=True)
    service = ChangeAnalystService(
        session_factory=session_factory,
        llm_change_tool=LlmChangeAnalysisTool(llm_client=TimeoutLlmClient()),
    )

    response = service.analyze_notice(
        NoticeSearchRequest(
            input_type="text",
            title="산업안전보건법 일부개정안",
            body='제23조 중 "매년 1회 이상 점검하여야 한다."을 "6개월마다 1회 이상 점검하여야 한다."으로 한다.',
        )
    )

    article_diff = response.article_diffs[0]
    assert article_diff.analysis_method == "rule_based"
    assert article_diff.after_text == "6개월마다 1회 이상 점검하여야 한다."
    assert any(
        item.tool_name == "classify_change_labels"
        and item.status == "error"
        and item.output_summary == "RuntimeError: LLM request timed out while calling https://example.test"
        for item in response.tool_audit
    )
