from __future__ import annotations

from contextlib import AbstractContextManager
import logging
import time
from typing import Callable

from app.clients.moleg_api import MolegApiClient
from app.db.repositories.articles import ArticleRepository
from app.db.repositories.laws import LawRepository
from app.db.repositories.runs import IngestRunRepository
from app.db.session import SessionLocal
from app.schemas.admin import IngestLawsResponse
from app.services.law_parser import LawXmlParser


logger = logging.getLogger(__name__)


class LawIngestService:
    def __init__(
        self,
        client: MolegApiClient | None = None,
        parser: LawXmlParser | None = None,
        session_factory: Callable[[], AbstractContextManager] = SessionLocal,
    ) -> None:
        self.client = client or MolegApiClient()
        self.parser = parser or LawXmlParser()
        self.session_factory = session_factory

    def ingest(self, targets: list[str]) -> IngestLawsResponse:
        started_at = time.perf_counter()
        failed_targets: list[str] = []
        laws_upserted = 0
        articles_upserted = 0

        logger.info("Starting law ingest run", extra={"targets": targets, "target_count": len(targets)})

        with self.session_factory() as session:
            run_repo = IngestRunRepository(session)
            run = run_repo.create(run_type="LAW_INGEST", target_scope=",".join(targets))
            law_repo = LawRepository(session)
            article_repo = ArticleRepository(session)
            logger.info("Created ingest run", extra={"run_id": run.id, "run_type": run.run_type})

            try:
                for target in targets:
                    target_started_at = time.perf_counter()
                    logger.info("Processing ingest target", extra={"run_id": run.id, "target": target})
                    try:
                        logger.info("Searching law metadata", extra={"run_id": run.id, "target": target})
                        summaries = self.client.search_law(target)
                        if not isinstance(summaries, list):
                            summaries = [summaries]
                        logger.info(
                            "Resolved law metadata",
                            extra={
                                "run_id": run.id,
                                "target": target,
                                "matched_count": len(summaries),
                                "matched_laws": [summary.law_name for summary in summaries],
                            },
                        )
                        upserted_articles_for_target = 0
                        for summary in summaries:
                            detail_path = getattr(summary, "detail_path", None)
                            detail_params = getattr(summary, "detail_params", None)
                            logger.info(
                                "Fetching law detail",
                                extra={
                                    "run_id": run.id,
                                    "target": target,
                                    "law_code": summary.law_code,
                                    "law_name": summary.law_name,
                                    "detail_path": detail_path,
                                    "detail_params": detail_params,
                                },
                            )
                            detail_root = self._fetch_law_detail(summary)
                            logger.info(
                                "Fetched law detail",
                                extra={
                                    "run_id": run.id,
                                    "target": target,
                                    "law_code": summary.law_code,
                                    "law_name": summary.law_name,
                                },
                            )

                            parsed = self.parser.parse_law(
                                detail_root,
                                fallback_law_name=summary.law_name,
                                fallback_law_code=summary.law_code,
                                fallback_law_type=summary.law_type,
                            )
                            logger.info(
                                "Parsed law detail",
                                extra={
                                    "run_id": run.id,
                                    "target": target,
                                    "parsed_law_name": parsed.law["law_name"],
                                    "parsed_law_type": parsed.law["law_type"],
                                    "article_count": len(parsed.articles),
                                },
                            )

                            law = law_repo.upsert(parsed.law)
                            logger.info(
                                "Upserted law metadata",
                                extra={
                                    "run_id": run.id,
                                    "target": target,
                                    "law_id": law.id,
                                    "law_code": law.law_code,
                                    "law_name": law.law_name,
                                },
                            )

                            article_records = []
                            for article in parsed.articles:
                                record = dict(article)
                                record["law_id"] = law.id
                                article_records.append(record)

                            upserted_articles_for_target += article_repo.upsert_many(article_records)
                            laws_upserted += 1

                        articles_upserted += upserted_articles_for_target
                        logger.info(
                            "Completed ingest target",
                            extra={
                                "run_id": run.id,
                                "target": target,
                                "matched_count": len(summaries),
                                "articles_upserted_for_target": upserted_articles_for_target,
                                "elapsed_ms": round((time.perf_counter() - target_started_at) * 1000, 2),
                                "laws_upserted_total": laws_upserted,
                                "articles_upserted_total": articles_upserted,
                            },
                        )
                    except Exception:
                        failed_targets.append(target)
                        logger.exception(
                            "Failed ingest target",
                            extra={"run_id": run.id, "target": target, "failed_targets": failed_targets},
                        )

                status = "SUCCESS" if not failed_targets else "FAILED"
                run_repo.finish(
                    run,
                    status=status,
                    summary={
                        "laws_upserted": laws_upserted,
                        "articles_upserted": articles_upserted,
                        "failed_targets": failed_targets,
                    },
                )
                session.commit()
                logger.info(
                    "Finished law ingest run",
                    extra={
                        "run_id": run.id,
                        "status": status,
                        "laws_upserted": laws_upserted,
                        "articles_upserted": articles_upserted,
                        "failed_targets": failed_targets,
                        "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 2),
                    },
                )
                return IngestLawsResponse(
                    run_id=run.id,
                    status=status,
                    laws_upserted=laws_upserted,
                    articles_upserted=articles_upserted,
                    failed_targets=failed_targets,
                )
            except Exception:
                run_repo.finish(run, status="FAILED", summary={"failed_targets": failed_targets})
                session.commit()
                logger.exception(
                    "Law ingest run aborted unexpectedly",
                    extra={
                        "run_id": run.id,
                        "failed_targets": failed_targets,
                        "laws_upserted": laws_upserted,
                        "articles_upserted": articles_upserted,
                    },
                )
                raise

    def _fetch_law_detail(self, summary):
        try:
            return self.client.fetch_law_detail(summary)
        except TypeError:
            return self.client.fetch_law_detail(summary.law_code)
