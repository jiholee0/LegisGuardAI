from __future__ import annotations

from contextlib import AbstractContextManager
from typing import Callable

import chromadb

from app.core.config import get_settings
from app.db.repositories.articles import ArticleRepository
from app.db.repositories.runs import IngestRunRepository
from app.db.session import SessionLocal
from app.schemas.admin import ReindexResponse
from app.services.chunking import chunk_article
from app.services.embeddings import build_embedding_provider


class EmbeddingIndexService:
    def __init__(self, session_factory: Callable[[], AbstractContextManager] = SessionLocal) -> None:
        self.session_factory = session_factory
        self.settings = get_settings()
        self.embedding_provider = build_embedding_provider()
        self.chroma_client = chromadb.PersistentClient(path=self.settings.chroma_persist_dir)

    def reindex(self, recreate: bool) -> ReindexResponse:
        with self.session_factory() as session:
            run_repo = IngestRunRepository(session)
            run = run_repo.create(run_type="REINDEX", target_scope=self.settings.chroma_collection_name)
            article_repo = ArticleRepository(session)
            articles = article_repo.list_all()

            if recreate:
                try:
                    self.chroma_client.delete_collection(self.settings.chroma_collection_name)
                except Exception:
                    pass
            collection = self.chroma_client.get_or_create_collection(self.settings.chroma_collection_name)

            documents: list[str] = []
            ids: list[str] = []
            metadatas: list[dict] = []

            for article in articles:
                for chunk in chunk_article(article):
                    if not chunk["text"].strip():
                        continue
                    documents.append(chunk["text"])
                    ids.append(chunk["id"])
                    metadatas.append(
                        {
                            "article_id": article.id,
                            "law_name": article.law.law_name,
                            "law_type": article.law.law_type,
                            "article_no": article.article_no,
                            "article_key": article.article_key,
                            "effective_date": article.effective_date.isoformat() if article.effective_date else "",
                            "chunk_type": chunk["chunk_type"],
                            "chunk_order": chunk["chunk_order"],
                            "text_hash": article.hash,
                            "display_text": chunk["display_text"],
                        }
                    )

            embeddings = self.embedding_provider.embed_documents(documents) if documents else []
            if ids:
                collection.upsert(ids=ids, documents=documents, metadatas=metadatas, embeddings=embeddings)

            run_repo.finish(run, status="SUCCESS", summary={"chunks_indexed": len(ids)})
            session.commit()
            return ReindexResponse(run_id=run.id, status="SUCCESS", chunks_indexed=len(ids))
