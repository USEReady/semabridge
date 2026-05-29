from __future__ import annotations

import os
import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, inspect, select, update
from sqlalchemy.orm import Session
from pydantic import BaseModel

from semabridge.api.deps import get_current_user, get_db
from semabridge.repository.orm.models import Account, Project, User
from semabridge.utils.logger import get_logger
from semabridge.auth.encryption import encrypt_token

logger = get_logger(__name__)


def _try_get_user(request: Request, db: Session) -> Optional[User]:
    """Resolve the current user when auth is enabled, return None otherwise.

    This allows the account router to work in both authenticated (production)
    and unauthenticated (development) modes without duplicating every endpoint.
    """
    if os.environ.get("AUTH_ENABLED", "true").lower() != "true":
        return None
    try:
        return get_current_user(request, db)
    except HTTPException:
        raise

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
def create_account(request: Request, body: AccountCreate, db: Session = Depends(get_db)):
    """Create a new tagged identity after successful OAuth/auth and return updated list."""
    import traceback
    user = _try_get_user(request, db)
    try:
        connection_id = str(body.connection_id or "").strip() or str(uuid.uuid4())

        existing_id = db.execute(
            select(Account).where(Account.id == connection_id)
        ).scalar_one_or_none()
        if existing_id:
            raise HTTPException(status_code=400, detail=f"Account with id '{connection_id}' already exists.")

        stmt = select(Account).where(Account.tag == body.tag)
        if user:
            stmt = stmt.where(Account.owner_id == user.id)
        existing = db.execute(stmt).scalar_one_or_none()

        if existing:
            if existing.connector_type == body.connector_type.upper():
                connector = body.connector_type.upper()
                safe_token = None

                if body.credentials and connector in ("SNOWFLAKE", "DATABRICKS"):
                    import json

                    clean_creds = {k: v for k, v in body.credentials.items() if v}
                    safe_token = encrypt_token(json.dumps(clean_creds))
                    logger.info(
                        "Refreshed stored credential bundle for %s account %s (%d keys)",
                        connector,
                        body.tag,
                        len(clean_creds),
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

                existing.identity_email = body.identity_email or existing.identity_email
                if safe_token is not None:
                    existing.encrypted_token = safe_token
                existing.status = "Active"
                db.commit()
                logger.info(
                    "Account with tag %s already exists for connector %s; refreshed stored credentials when provided",
                    body.tag,
                    body.connector_type.upper(),
                )
                return get_accounts(request=request, connector_type=body.connector_type, db=db)
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Account tag '{body.tag}' is already used by connector "
                    f"'{existing.connector_type}'."
                ),
            )

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
            owner_id=user.id if user else None,  # Multi-user ownership
        )
        db.add(new_account)
        db.commit()
        logger.info(
            f"Created new account {new_account.tag} ({new_account.connector_type})"
        )

        # Return updated list so the UI can refresh in one round-trip
        return get_accounts(request=request, connector_type=body.connector_type, db=db)
    except HTTPException:
        raise
    except Exception:
        db.rollback()
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=500, content={"detail": f"CRASH: {traceback.format_exc()}"})


@router.get("", response_model=List[AccountResponse])
def get_accounts(
    request: Request,
    connector_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Fetch accounts, optionally filtered by connector_type.

    When auth is enabled, only accounts owned by the current user are returned.
    """
    user = _try_get_user(request, db)
    stmt = select(Account)
    if user:
        stmt = stmt.where(Account.owner_id == user.id)
    if connector_type:
        stmt = stmt.where(Account.connector_type == connector_type.upper())
    
    accounts = db.execute(stmt).scalars().all()
    return accounts


@router.patch("/project/{project_id}/link-account")
def link_project_account(
    request: Request,
    project_id: str,
    body: ProjectAccountLink,
    db: Session = Depends(get_db),
):
    """Link a specific accountId to a Project and store the specific data-level settings."""
    user = _try_get_user(request, db)
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

    # Ownership check — ensure the account belongs to the requesting user
    if user and account.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Account not found or access denied")

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
                    main_module._compat_save_project_yaml_text(project_id, main_module._compat_project_configs[project_id])
    except Exception as exc:
        logger.warning("Could not sync compatibility project/account state for %s: %s", project_id, exc)
    
    logger.info(f"Linked Project {project_id} to Account {account.tag}")
    return {"status": "success", "project_id": project_id, "account_id": account.id}


@router.delete("/{account_id}", response_model=List[AccountResponse])
def delete_account(request: Request, account_id: str, db: Session = Depends(get_db)):
    """Delete the credentials for a specific account ID and return updated list."""
    user = _try_get_user(request, db)
    account = db.execute(
        select(Account).where(Account.id == account_id)
    ).scalar_one_or_none()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Ownership check
    if user and account.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Account not found or access denied")

    connector_type = account.connector_type

    bind = db.get_bind()
    has_project_account_link = False
    if bind is not None:
        inspector = inspect(bind)
        has_project_account_link = (
            inspector.has_table('projects')
            and any(col.get('name') == 'account_id' for col in inspector.get_columns('projects'))
        )

    if has_project_account_link:
        db.execute(
            update(Project)
            .where(Project.account_id == account.id)
            .values(account_id=None)
        )
        db.flush()

    # Use SQL-level delete to avoid ORM relationship lazy-loads against
    # legacy projects schemas that may miss newer optional columns.
    db.execute(delete(Account).where(Account.id == account.id))
    db.commit()
    logger.info(f"Deleted Account {account_id} ({account.tag})")
    
    # Return updated list
    return get_accounts(request=request, connector_type=connector_type, db=db)


@router.patch("/{account_id}/tag", response_model=List[AccountResponse])
def update_account_tag(request: Request, account_id: str, body: AccountTagUpdate, db: Session = Depends(get_db)):
    """Rename the Tag of an account and return updated list."""
    user = _try_get_user(request, db)
    account = db.execute(
        select(Account).where(Account.id == account_id)
    ).scalar_one_or_none()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Ownership check
    if user and account.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Account not found or access denied")

    # Check for tag duplication
    stmt = select(Account).where(Account.tag == body.tag)
    if user:
        stmt = stmt.where(Account.owner_id == user.id)
    existing = db.execute(stmt).scalar_one_or_none()

    if existing and existing.id != account_id:
        raise HTTPException(status_code=400, detail=f"Tag '{body.tag}' is already in use.")

    account.tag = body.tag
    connector_type = account.connector_type
    db.commit()
    
    logger.info(f"Updated Account {account_id} tag to {account.tag}")
    
    # Return updated list
    return get_accounts(request=request, connector_type=connector_type, db=db)



