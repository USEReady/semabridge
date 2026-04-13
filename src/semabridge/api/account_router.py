from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session
from pydantic import BaseModel

from semabridge.api.deps import get_db
from semabridge.repository.orm.models import Account, Project
from semabridge.utils.logger import get_logger
from semabridge.auth.encryption import encrypt_token

logger = get_logger(__name__)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

class AccountCreate(BaseModel):
    connection_id: Optional[str] = None
    connector_type: str
    tag: str
    identity_email: Optional[str] = None
    encrypted_token: Optional[str] = None
    credentials: Optional[dict] = None  # Full credential bundle (Snowflake/Databricks)

class AccountResponse(BaseModel):
    id: str
    connector_type: str
    tag: str
    identity_email: Optional[str]
    status: str
    is_default: bool

    class Config:
        from_attributes = True

class ProjectAccountLink(BaseModel):
    account_id: str
    warehouse: Optional[str] = None
    database: Optional[str] = None
    db_schema: Optional[str] = None

class AccountTagUpdate(BaseModel):
    tag: str



@router.post("", response_model=List[AccountResponse], status_code=status.HTTP_201_CREATED)
def create_account(body: AccountCreate, db: Session = Depends(get_db)):
    """Create a new tagged identity after successful OAuth/auth and return updated list."""
    import traceback
    try:
        connection_id = str(body.connection_id or "").strip() or str(uuid.uuid4())

        existing_id = db.execute(
            select(Account).where(Account.id == connection_id)
        ).scalar_one_or_none()
        if existing_id:
            raise HTTPException(status_code=400, detail=f"Account with id '{connection_id}' already exists.")

        existing = db.execute(
            select(Account).where(Account.tag == body.tag)
        ).scalar_one_or_none()

        if existing:
            raise HTTPException(status_code=400, detail=f"Account with tag '{body.tag}' already exists.")

        # Encrypt the credential bundle at rest
        safe_token = None
        connector = body.connector_type.upper()

        if body.credentials and connector in ("SNOWFLAKE", "DATABRICKS"):
            # New path: full credential bundle as JSON
            import json
            # Strip empty values to keep the bundle clean
            clean_creds = {k: v for k, v in body.credentials.items() if v}
            payload_str = json.dumps(clean_creds)
            safe_token = encrypt_token(payload_str)
            logger.info(
                "Stored full credential bundle for %s account %s (%d keys)",
                connector, body.tag, len(clean_creds),
            )
        elif body.encrypted_token:
            payload_str = body.encrypted_token
            if connector == "FABRIC":
                try:
                    import json
                    from semabridge.repository.credential_manager import CredentialManager
                    cm = CredentialManager()
                    full_token = cm.get_msal_token()
                    if full_token and full_token.get("access_token") == payload_str:
                        payload_str = json.dumps(full_token)
                except Exception as e:
                    logger.warning(f"Could not merge full MSAL payload for account {body.tag}: {e}")
            
            safe_token = encrypt_token(payload_str)

        new_account = Account(
            id=connection_id,
            connector_type=body.connector_type.upper(),
            tag=body.tag,
            identity_email=body.identity_email,
            encrypted_token=safe_token,
            status="Active",
            is_default=False,
        )
        db.add(new_account)
        db.commit()
        logger.info(
            f"Created new account {new_account.tag} ({new_account.connector_type})"
        )

        # Return updated list so the UI can refresh in one round-trip
        return get_accounts(connector_type=body.connector_type, db=db)
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"detail": f"CRASH: {traceback.format_exc()}"})


@router.get("", response_model=List[AccountResponse])
def get_accounts(connector_type: Optional[str] = None, db: Session = Depends(get_db)):
    """Fetch accounts, optionally filtered by connector_type (e.g., FABRIC or SNOWFLAKE)."""
    stmt = select(Account)
    if connector_type:
        stmt = stmt.where(Account.connector_type == connector_type.upper())
    
    accounts = db.execute(stmt).scalars().all()
    return accounts


@router.patch("/project/{project_id}/link-account")
def link_project_account(project_id: str, body: ProjectAccountLink, db: Session = Depends(get_db)):
    """Link a specific accountId to a Project and store the specific data-level settings."""
    project = db.execute(
        select(Project).where(Project.project_id == project_id)
    ).scalar_one_or_none()

    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    account = db.execute(
        select(Account).where(Account.id == body.account_id)
    ).scalar_one_or_none()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    project.account_id = account.id
    if body.warehouse is not None:
        project.warehouse = body.warehouse
    if body.database is not None:
        project.database = body.database
    if body.schema is not None:
        project.schema = body.schema
    
    db.commit()
    db.refresh(project)

    try:
        import semabridge.api.main as main_module
        if hasattr(main_module, "_compat_projects") and project_id in main_module._compat_projects:
            main_module._compat_projects[project_id]["account_id"] = account.id
            main_module._compat_projects[project_id]["updated_at"] = main_module._compat_now_iso()
        if hasattr(main_module, "_compat_project_configs") and project_id in main_module._compat_project_configs:
            yaml_text = main_module._compat_project_configs.get(project_id) or ""
            if yaml_text:
                import yaml
                parsed = yaml.safe_load(yaml_text) or {}
                if isinstance(parsed, dict):
                    source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
                    source_cfg["identity_id"] = account.id
                    parsed["source"] = source_cfg
                    main_module._compat_project_configs[project_id] = yaml.safe_dump(parsed, sort_keys=False, allow_unicode=False)
                    main_module._compat_save_repo_yaml_text(main_module._compat_project_configs[project_id])
    except Exception as exc:
        logger.warning("Could not sync compatibility project/account state for %s: %s", project_id, exc)
    
    logger.info(f"Linked Project {project_id} to Account {account.tag}")
    return {"status": "success", "project_id": project_id, "account_id": account.id}


@router.delete("/{account_id}", response_model=List[AccountResponse])
def delete_account(account_id: str, db: Session = Depends(get_db)):
    """Delete the credentials for a specific account ID and return updated list."""
    account = db.execute(
        select(Account).where(Account.id == account_id)
    ).scalar_one_or_none()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    connector_type = account.connector_type
    db.delete(account)
    db.commit()
    logger.info(f"Deleted Account {account_id} ({account.tag})")
    
    # Return updated list
    return get_accounts(connector_type=connector_type, db=db)


@router.patch("/{account_id}/tag", response_model=List[AccountResponse])
def update_account_tag(account_id: str, body: AccountTagUpdate, db: Session = Depends(get_db)):
    """Rename the Tag of an account and return updated list."""
    account = db.execute(
        select(Account).where(Account.id == account_id)
    ).scalar_one_or_none()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Check for tag duplication
    existing = db.execute(
        select(Account).where(Account.tag == body.tag)
    ).scalar_one_or_none()

    if existing and existing.id != account_id:
        raise HTTPException(status_code=400, detail=f"Tag '{body.tag}' is already in use.")

    account.tag = body.tag
    connector_type = account.connector_type
    db.commit()
    
    logger.info(f"Updated Account {account_id} tag to {account.tag}")
    
    # Return updated list
    return get_accounts(connector_type=connector_type, db=db)



