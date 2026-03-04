from __future__ import annotations

from datetime import datetime, timezone
import json

from sqlalchemy.orm import Session

from app.db.models import IngestRun


class IngestRunRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create(self, run_type: str, target_scope: str, status: str = "STARTED") -> IngestRun:
        run = IngestRun(run_type=run_type, target_scope=target_scope, status=status)
        self.session.add(run)
        self.session.flush()
        return run

    def finish(self, run: IngestRun, status: str, summary: dict | None = None) -> IngestRun:
        run.status = status
        run.summary_json = json.dumps(summary or {}, ensure_ascii=False)
        run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
        self.session.add(run)
        self.session.flush()
        return run
