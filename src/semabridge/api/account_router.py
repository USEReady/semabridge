from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from pydantic import BaseModel

from semabridge.api.deps import get_db
from semabridge.repository.orm.models import Account, Project
from semabridge.utils.logger import get_logger
from semabridge.auth.encryption import encrypt_token

logger = get_logger(__name__)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])

class AccountCreate(BaseModel):
    connector_type: str
    tag: str
    identity_email: Optional[str] = None
    encrypted_token: Optional[str] = None

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
    """Create a new tagged identity after successful OAuth/auth and return updated list.

    Enforces "one default at a time": the newly created account is automatically
    promoted to default and all previous accounts of the same connector type are
    demoted.  This avoids any UI confusion around which identity is active.
    """
    import traceback
    try:
        existing = db.execute(
            select(Account).where(Account.tag == body.tag)
        ).scalar_one_or_none()

        if existing:
            raise HTTPException(status_code=400, detail=f"Account with tag '{body.tag}' already exists.")

        # Demote all existing accounts of the same connector type before inserting
        # the new one so there is never more than one default.
        db.execute(
            update(Account)
            .where(Account.connector_type == body.connector_type.upper())
            .values(is_default=False)
        )

        # Encrypt the token at rest
        safe_token = encrypt_token(body.encrypted_token) if body.encrypted_token else None

        new_account = Account(
            id=str(uuid.uuid4()),
            connector_type=body.connector_type.upper(),
            tag=body.tag,
            identity_email=body.identity_email,
            encrypted_token=safe_token,
            status="Active",
            is_default=True,  # New account always becomes the default
        )
        db.add(new_account)
        db.commit()
        logger.info(
            f"Created new account {new_account.tag} ({new_account.connector_type}) "
            "and set as default."
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


@router.patch("/{account_id}/default")
def set_default_account(account_id: str, db: Session = Depends(get_db)):
    """Set the given account as the default for its connector type and return updated list."""
    account = db.execute(
        select(Account).where(Account.id == account_id)
    ).scalar_one_or_none()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    # Clear defaults for all other accounts of the same type
    db.execute(
        update(Account)
        .where(Account.connector_type == account.connector_type)
        .values(is_default=False)
    )

    # Set this one to true
    account.is_default = True
    connector_type = account.connector_type
    db.commit()

    logger.info(f"Set Account {account_id} ({account.tag}) as default {account.connector_type}")
    
    # Flush global session token and all memoized caches
    try:
        import semabridge.api.main as main_module
        
        # Purge MSAL Instance cache and global UI instances
        if hasattr(main_module, "clear_msal_cache"):
            main_module.clear_msal_cache()
            
        # Purge Fabric Workspace & Model Discovery Cache
        if hasattr(main_module, "_discovery_cache") and isinstance(main_module._discovery_cache, dict):
            main_module._discovery_cache.clear()
            
        # Purge Snowflake Discovery Cache
        if hasattr(main_module, "_snowflake_discovery_cache") and isinstance(main_module._snowflake_discovery_cache, dict):
            main_module._snowflake_discovery_cache.clear()
            
        logger.info("Successfully invalidated all application-level caches due to Identity change")
    except Exception as e:
        logger.warning(f"Could not fully flush global session state: {e}")

    # Return updated list
    return get_accounts(connector_type=connector_type, db=db)

