from __future__ import annotations

import time
from contextlib import contextmanager
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.agent_runs import get_agent_run_manager
from app.db.models import Article, Base, Law
from app.main import app
from app.core.config import get_settings
from app.services.agents.change_analyst import ChangeAnalystService
from app.services.agents.run_manager import AgentRunManager
from app.services.agents.tools.llm_notice_parser import LlmNoticeParserTool
from app.services.law.embedding_index import EmbeddingIndexService


class FakeNoticeParserLlmClient:
    def generate_json(self, *, system_prompt: str, user_prompt: str) -> dict:
        return {
            "law_name": "산업안전보건법",
            "change_types": ["일부개정"],
            "analysis_mode": "DIFF",
            "article_candidates": [
                {
                    "article_no": "제23조",
                    "article_ref_text": "제23조",
                    "change_type": "일부개정",
                    "analysis_mode": "DIFF",
                    "source_text": '제23조 중 "매년 1회 이상 점검하여야 한다."을 "6개월마다 1회 이상 점검하여야 한다."으로 한다.',
                }
            ],
        }


def build_session_factory(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agent_runs.db'}", future=True)
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


def test_agent_runs_snapshot_and_events(tmp_path: Path):
    session_factory = build_session_factory(tmp_path)
    seed_articles(session_factory)

    settings = get_settings()
    settings.chroma_persist_dir = str(tmp_path / "chroma_agent_runs")
    settings.chroma_collection_name = "agent_runs_articles"

    EmbeddingIndexService(session_factory=session_factory).reindex(recreate=True)

    manager = AgentRunManager(
        parser=LlmNoticeParserTool(llm_client=FakeNoticeParserLlmClient()),
        change_analyst_service=ChangeAnalystService(session_factory=session_factory),
    )
    app.dependency_overrides[get_agent_run_manager] = lambda: manager

    client = TestClient(app)
    create_response = client.post(
        "/agent/runs",
        json={
            "input": {
                "type": "text",
                "raw": {
                    "content": '제23조 중 "매년 1회 이상 점검하여야 한다."을 "6개월마다 1회 이상 점검하여야 한다."으로 한다.',
                    "metadata": {"title": "산업안전보건법 일부개정안"},
                },
            }
        },
    )
    assert create_response.status_code == 200
    run_id = create_response.json()["run_id"]

    snapshot = None
    for _ in range(60):
        snapshot_response = client.get(f"/agent/runs/{run_id}")
        assert snapshot_response.status_code == 200
        snapshot = snapshot_response.json()
        if snapshot["status"] in {"completed", "failed"}:
            break
        time.sleep(0.1)

    assert snapshot is not None
    assert snapshot["status"] == "completed"
    assert snapshot["input"]["normalized"]["law_name"] == "산업안전보건법"
    assert snapshot["shared_context"]["analysis_mode"] == "DIFF"
    assert snapshot["shared_context"]["article_candidates"][0]["source_text"]
    assert snapshot["agents"][0]["agent"] == "orchestrator"
    assert snapshot["agents"][0]["status"] == "completed"
    assert snapshot["agents"][1]["agent"] == "change_analyst"
    assert snapshot["agents"][1]["status"] == "completed"
    assert snapshot["final_result"]["report"]["total_changes"] == 1

    events_response = client.get(f"/agent/runs/{run_id}/events?follow=false")
    assert events_response.status_code == 200
    body = events_response.text
    assert "event: run.status" in body
    assert "event: agent.status" in body
    assert "event: final.result" in body

    app.dependency_overrides.clear()
