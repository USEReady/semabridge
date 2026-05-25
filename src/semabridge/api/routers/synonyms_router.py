from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from semabridge.api.deps import get_db
from semabridge.repository.orm.models import SynonymOverride

router = APIRouter(prefix="/api/synonyms", tags=["synonyms"])


class SynonymOverrideRequest(BaseModel):
    project_id: str
    model_name: str
    table_name: str
    column_name: str
    synonyms: List[str] = Field(default_factory=list)


def _clean_synonyms(values: List[str]) -> List[str]:
    cleaned: List[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        key = text.casefold()
        if text and key not in seen:
            seen.add(key)
            cleaned.append(text)
    return cleaned


def _find_row(
    db: Session,
    project_id: str,
    model_name: str,
    table_name: str,
    column_name: str,
) -> SynonymOverride | None:
    return db.execute(
        select(SynonymOverride).where(
            SynonymOverride.project_id == project_id,
            SynonymOverride.model_name == model_name,
            SynonymOverride.table_name == table_name,
            SynonymOverride.column_name == column_name,
        )
    ).scalar_one_or_none()


@router.get("/{project_id}/{model_name}/{table_name}/{column_name}")
def get_synonym_override(
    project_id: str,
    model_name: str,
    table_name: str,
    column_name: str,
    db: Session = Depends(get_db),
):
    try:
        row = _find_row(db, project_id, model_name, table_name, column_name)
        return {"synonyms": row.synonyms if row else []}
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail=f"Synonym overrides unavailable: {exc}") from exc


@router.post("/")
def upsert_synonym_override(req: SynonymOverrideRequest, db: Session = Depends(get_db)):
    try:
        synonyms = _clean_synonyms(req.synonyms)
        row = _find_row(db, req.project_id, req.model_name, req.table_name, req.column_name)
        if row is None:
            row = SynonymOverride(
                project_id=req.project_id,
                model_name=req.model_name,
                table_name=req.table_name,
                column_name=req.column_name,
            )
            db.add(row)
        row.synonyms = synonyms
        db.commit()
        db.refresh(row)
        return {"status": "success", "id": row.id, "synonyms": row.synonyms}
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=f"Synonym overrides unavailable: {exc}") from exc


@router.delete("/{project_id}/{model_name}/{table_name}/{column_name}")
def delete_synonym_override(
    project_id: str,
    model_name: str,
    table_name: str,
    column_name: str,
    db: Session = Depends(get_db),
):
    try:
        row = _find_row(db, project_id, model_name, table_name, column_name)
        if row is not None:
            db.delete(row)
            db.commit()
        return {"status": "deleted"}
    except SQLAlchemyError as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail=f"Synonym overrides unavailable: {exc}") from exc
