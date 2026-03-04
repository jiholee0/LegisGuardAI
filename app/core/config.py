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
    )
    settings.ensure_data_dirs()
    return settings
