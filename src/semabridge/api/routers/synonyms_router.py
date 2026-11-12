from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from semabridge.api.deps import get_db
from semabridge.repository.orm.models import SynonymOverride

router = APIRouter(prefix="/api/synonyms", tags=["synonyms"])


class SynonymOverrideRequest(BaseModel):
    """PATCH-style upsert: only fields present in the request body change.

    An explicit empty string / empty list / null clears that field; an omitted
    field keeps its stored value.
    """

    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(..., min_length=1)
    model_name: str = Field(..., min_length=1)
    table_name: str = Field(..., min_length=1)
    column_name: str = Field(..., min_length=1)
    synonyms: Optional[List[str]] = None
    description: Optional[str] = None
    business_rules: Optional[List[str]] = None
    data_steward: Optional[str] = None

    @field_validator("project_id", "model_name", "table_name", "column_name")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not str(v or "").strip():
            raise ValueError("must not be empty")
        return v.strip()


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


@router.get("/{project_id}/{model_name}")
def get_model_ontology_overrides(
    project_id: str,
    model_name: str,
    db: Session = Depends(get_db),
):
    """Retrieve all ontology overrides for a model."""
    try:
        rows = db.execute(
            select(SynonymOverride).where(
                SynonymOverride.project_id == project_id,
                SynonymOverride.model_name == model_name,
            )
        ).scalars().all()
        return [
            {
                "table_name": r.table_name,
                "column_name": r.column_name,
                "synonyms": r.synonyms,
                "description": r.description,
                "business_rules": r.business_rules,
                "data_steward": r.data_steward,
            }
            for r in rows
        ]
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail=f"Ontology overrides unavailable: {exc}") from exc


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
        return {
            "synonyms": row.synonyms if row else [],
            "description": row.description if row else None,
            "business_rules": row.business_rules if row else [],
            "data_steward": row.data_steward if row else None,
        }
    except SQLAlchemyError as exc:
        raise HTTPException(status_code=503, detail=f"Synonym overrides unavailable: {exc}") from exc


@router.post("/")
@router.patch("/")
def upsert_synonym_override(req: SynonymOverrideRequest, db: Session = Depends(get_db)):
    try:
        provided = req.model_fields_set
        row = _find_row(db, req.project_id, req.model_name, req.table_name, req.column_name)
        if row is None:
            row = SynonymOverride(
                project_id=req.project_id,
                model_name=req.model_name,
                table_name=req.table_name,
                column_name=req.column_name,
            )
            row.synonyms = []
            row.business_rules = []
            db.add(row)
        if "synonyms" in provided:
            row.synonyms = _clean_synonyms(req.synonyms or [])
        if "description" in provided:
            row.description = (req.description or "").strip() or None
        if "business_rules" in provided:
            row.business_rules = _clean_synonyms(req.business_rules or [])
        if "data_steward" in provided:
            row.data_steward = (req.data_steward or "").strip() or None
        db.commit()
        db.refresh(row)
        return {
            "status": "success",
            "id": row.id,
            "synonyms": row.synonyms,
            "description": row.description,
            "business_rules": row.business_rules,
            "data_steward": row.data_steward,
        }
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
