from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Law


class LawRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(self, record: dict) -> Law:
        law = self.session.scalar(select(Law).where(Law.law_code == record["law_code"]))
        if law is None:
            law = Law(**record)
            self.session.add(law)
        else:
            for key, value in record.items():
                setattr(law, key, value)
        self.session.flush()
        return law

    def list_all(self) -> list[Law]:
        return list(self.session.scalars(select(Law).order_by(Law.law_name)))
