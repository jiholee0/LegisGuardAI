from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover
    def load_dotenv(*args, **kwargs):  # type: ignore[no-redef]
        return False


@dataclass
class Settings:
    moleg_api_key: str | None
    moleg_search_url: str
    moleg_detail_url: str
    database_url: str
    chroma_persist_dir: str
    chroma_collection_name: str
    raw_cache_dir: str
    law_targets: str
    embedding_provider: str
    embedding_model_name: str
    embedding_dimension: int
    article_chunk_char_limit: int
    llm_provider: str
    llm_base_url: str | None
    llm_api_key: str | None
    llm_model: str
    llm_deployment: str | None
    llm_api_version: str
    llm_timeout_seconds: float

    @property
    def configured_targets(self) -> list[str]:
        return [item.strip() for item in self.law_targets.split(",") if item.strip()]

    def ensure_data_dirs(self) -> None:
        for path_str in [self.sqlite_path.parent, Path(self.chroma_persist_dir), Path(self.raw_cache_dir)]:
            path_str.mkdir(parents=True, exist_ok=True)

    @property
    def sqlite_path(self) -> Path:
        prefix = "sqlite:///"
        if self.database_url.startswith(prefix):
            return Path(self.database_url.removeprefix(prefix))
        return Path("data/sqlite/legisguard.db")


@lru_cache
def get_settings() -> Settings:
    load_dotenv()
    settings = Settings(
        moleg_api_key=os.getenv("MOLEG_API_KEY"),
        moleg_search_url=os.getenv("MOLEG_SEARCH_URL", "http://www.law.go.kr/DRF/lawSearch.do"),
        moleg_detail_url=os.getenv("MOLEG_DETAIL_URL", "http://www.law.go.kr/DRF/lawService.do"),
        database_url=os.getenv("DATABASE_URL", "sqlite:///data/sqlite/legisguard.db"),
        chroma_persist_dir=os.getenv("CHROMA_PERSIST_DIR", "data/chroma"),
        chroma_collection_name=os.getenv("CHROMA_COLLECTION_NAME", "legisguard_articles"),
        raw_cache_dir=os.getenv("RAW_CACHE_DIR", "data/raw"),
        law_targets=os.getenv(
            "LAW_TARGETS",
            "산업안전보건법,산업안전보건법 시행령,산업안전보건법 시행규칙",
        ),
        embedding_provider=os.getenv("EMBEDDING_PROVIDER", "hash"),
        embedding_model_name=os.getenv("EMBEDDING_MODEL_NAME", "BAAI/bge-m3"),
        embedding_dimension=int(os.getenv("EMBEDDING_DIMENSION", "256")),
        article_chunk_char_limit=int(os.getenv("ARTICLE_CHUNK_CHAR_LIMIT", "900")),
        llm_provider=os.getenv("LLM_PROVIDER", "disabled"),
        llm_base_url=os.getenv("LLM_BASE_URL"),
        llm_api_key=os.getenv("LLM_API_KEY"),
        llm_model=os.getenv("LLM_MODEL", "gpt-4.1-mini"),
        llm_deployment=os.getenv("LLM_DEPLOYMENT"),
        llm_api_version=os.getenv("LLM_API_VERSION", "2024-10-21"),
        llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "60")),
    )
    settings.ensure_data_dirs()
    return settings
