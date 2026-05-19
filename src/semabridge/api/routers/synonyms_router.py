# src/semabridge/api/routers/synonyms_router.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import List
from sqlalchemy.orm import Session

from semabridge.repository.orm.models import SynonymOverride
from semabridge.api.deps import get_db
 
router = APIRouter(prefix='/api/synonyms', tags=['synonyms'])
 
class SynonymOverrideRequest(BaseModel):
    project_id:  str
    model_name:  str
    table_name:  str
    column_name: str
    synonyms:    List[str]
 
@router.get('/{project_id}/{model_name}/{table_name}/{column_name}')
def get_synonym(project_id: str, model_name: str, table_name: str, column_name: str, db: Session = Depends(get_db)):
    row = db.query(SynonymOverride).filter_by(
        project_id=project_id, model_name=model_name,
        table_name=table_name, column_name=column_name
    ).first()
    return {'synonyms': row.synonyms if row else []}
 
@router.post('/')
def upsert_synonym(req: SynonymOverrideRequest, db: Session = Depends(get_db)):
    row = db.query(SynonymOverride).filter_by(
        project_id=req.project_id, model_name=req.model_name,
        table_name=req.table_name, column_name=req.column_name
    ).first()
    if row:
        row.synonyms = req.synonyms
    else:
        # Use req.model_dump() or req.dict() depending on Pydantic version.
        # Since project uses pydantic v2 (or v1 compatible), req.dict() is safe and standard in both.
        row = SynonymOverride(**req.dict())
        db.add(row)
    db.commit()
    return {'status': 'success', 'id': str(row.id)}
 
@router.delete('/{project_id}/{model_name}/{table_name}/{column_name}')
def delete_synonym(project_id: str, model_name: str, table_name: str, column_name: str, db: Session = Depends(get_db)):
    db.query(SynonymOverride).filter_by(
        project_id=project_id, model_name=model_name,
        table_name=table_name, column_name=column_name
    ).delete()
    db.commit()
    return {'status': 'deleted'}
