from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session
from pydantic import BaseModel

from semabridge.api.deps import get_current_user, get_db
from semabridge.repository.orm.models import Account, Project, User
from semabridge.utils.logger import get_logger
from semabridge.auth.encryption import decrypt_token, encrypt_token

logger = get_logger(__name__)


def _merge_credential_bundle(prior_encrypted_token: Optional[str], new_credentials: dict) -> dict:
    """Merge freshly submitted credentials on top of the previously stored bundle.

    Without this, saving a partial update (e.g. editing the warehouse without
    retyping a secret like private_key_passphrase, which the UI never
    pre-fills) would silently erase every field the user didn't resubmit,
    since the caller used to just re-encrypt ``new_credentials`` on its own.

    If the submission declares an ``auth_type``, keys exclusive to other auth
    modes are purged from the prior bundle first — otherwise switching e.g.
    password auth to keypair auth would resurrect the stale password via the
    merge instead of clearing it, mirroring the purge already performed by
    ``CredentialManager.save_credentials``.
    """
    import json

    from semabridge.repository.credential_manager import _AUTH_EXCLUSIVE_KEYS

    merged: dict = {}
    if prior_encrypted_token:
        try:
            decrypted = decrypt_token(prior_encrypted_token)
            if decrypted:
                prior = json.loads(decrypted)
                if isinstance(prior, dict):
                    merged.update(prior)
        except Exception:
            logger.debug("Could not decrypt prior credential bundle for merge", exc_info=True)

    new_auth_type = new_credentials.get("auth_type")
    if new_auth_type:
        for stale_key in _AUTH_EXCLUSIVE_KEYS.get(new_auth_type, []):
            merged.pop(stale_key, None)

    merged.update({k: v for k, v in new_credentials.items() if v})
    return merged


def _try_get_user(request: Request, db: Session) -> Optional[User]:
    """Resolve the current user when auth is enabled, return None otherwise.

    This allows the account router to work in both authenticated (production)
    and unauthenticated (development) modes without duplicating every endpoint.
    """
    from semabridge.auth.flags import auth_enabled

    if not auth_enabled():
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
    auth_type: Optional[str] = None
    token_expires_at: Optional[datetime] = None
    token_status: Optional[str] = None

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
                resolved_auth_type = None
                token_exp = None
                ref_token = None

                if body.credentials and connector in ("SNOWFLAKE", "DATABRICKS", "FABRIC"):
                    import json

                    clean_creds = _merge_credential_bundle(existing.encrypted_token, body.credentials)
                    safe_token = encrypt_token(json.dumps(clean_creds))
                    resolved_auth_type = clean_creds.get("auth_type", "service_principal" if connector == "FABRIC" else None)
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
                            from semabridge.api.services.connection_domain_service import get_and_consume_pending_fabric_login

                            pending = get_and_consume_pending_fabric_login(payload_str)
                            if pending:
                                payload_str = json.dumps(pending)
                                resolved_auth_type = pending.get("auth_method", "interactive")
                                if pending.get("expires_at"):
                                    token_exp = datetime.fromtimestamp(int(pending["expires_at"]), tz=timezone.utc)
                                if pending.get("refresh_token"):
                                    ref_token = encrypt_token(pending["refresh_token"])
                            else:
                                try:
                                    parsed_b = json.loads(payload_str)
                                    if isinstance(parsed_b, dict):
                                        resolved_auth_type = parsed_b.get("auth_method", parsed_b.get("auth_type", "interactive"))
                                        if parsed_b.get("expires_at"):
                                            token_exp = datetime.fromtimestamp(int(parsed_b["expires_at"]), tz=timezone.utc)
                                        if parsed_b.get("refresh_token"):
                                            ref_token = encrypt_token(parsed_b["refresh_token"])
                                except Exception:
                                    pass
                        except Exception as e:
                            logger.warning(f"Could not consume pending Fabric login for account {body.tag}: {e}")

                    safe_token = encrypt_token(payload_str)

                existing.identity_email = body.identity_email or existing.identity_email
                if safe_token is not None:
                    existing.encrypted_token = safe_token
                if resolved_auth_type is not None:
                    existing.auth_type = resolved_auth_type
                if token_exp is not None:
                    existing.token_expires_at = token_exp
                if ref_token is not None:
                    existing.refresh_token = ref_token
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
        resolved_auth_type = None
        token_exp = None
        ref_token = None

        if body.credentials and connector in ("SNOWFLAKE", "DATABRICKS", "FABRIC"):
            # New path: full credential bundle as JSON
            import json
            # No prior row exists for this tag — merge is a no-op beyond
            # stripping empty values, but reuses the same helper for symmetry.
            clean_creds = _merge_credential_bundle(None, body.credentials)
            payload_str = json.dumps(clean_creds)
            safe_token = encrypt_token(payload_str)
            resolved_auth_type = clean_creds.get("auth_type", "service_principal" if connector == "FABRIC" else None)
            logger.info(
                "Stored full credential bundle for %s account %s (%d keys)",
                connector, body.tag, len(clean_creds),
            )
        elif body.encrypted_token:
            payload_str = body.encrypted_token
            if connector == "FABRIC":
                try:
                    import json
                    from semabridge.api.services.connection_domain_service import get_and_consume_pending_fabric_login

                    pending = get_and_consume_pending_fabric_login(payload_str)
                    if pending:
                        payload_str = json.dumps(pending)
                        resolved_auth_type = pending.get("auth_method", "interactive")
                        if pending.get("expires_at"):
                            token_exp = datetime.fromtimestamp(int(pending["expires_at"]), tz=timezone.utc)
                        if pending.get("refresh_token"):
                            ref_token = encrypt_token(pending["refresh_token"])
                    else:
                        try:
                            parsed_b = json.loads(payload_str)
                            if isinstance(parsed_b, dict):
                                resolved_auth_type = parsed_b.get("auth_method", parsed_b.get("auth_type", "interactive"))
                                if parsed_b.get("expires_at"):
                                    token_exp = datetime.fromtimestamp(int(parsed_b["expires_at"]), tz=timezone.utc)
                                if parsed_b.get("refresh_token"):
                                    ref_token = encrypt_token(parsed_b["refresh_token"])
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning(f"Could not consume pending Fabric login for account {body.tag}: {e}")
            
            safe_token = encrypt_token(payload_str)

        new_account = Account(
            id=connection_id,
            connector_type=body.connector_type.upper(),
            tag=body.tag,
            identity_email=body.identity_email,
            encrypted_token=safe_token,
            refresh_token=ref_token,
            token_expires_at=token_exp,
            auth_type=resolved_auth_type,
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
    now = datetime.now(tz=timezone.utc)
    res = []
    for acct in accounts:
        auth_type = acct.auth_type
        expires_at = acct.token_expires_at
        token_status = "valid"
        if acct.connector_type == "FABRIC":
            if auth_type == "service_principal":
                token_status = "valid"
            elif expires_at:
                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(tzinfo=timezone.utc)
                token_status = "expired" if now >= expires_at else "valid"
            elif acct.encrypted_token:
                try:
                    dec = decrypt_token(acct.encrypted_token)
                    import json

                    b = json.loads(dec)
                    if isinstance(b, dict):
                        if b.get("auth_type") == "service_principal":
                            auth_type = "service_principal"
                            token_status = "valid"
                        elif b.get("expires_at"):
                            exp_dt = datetime.fromtimestamp(int(b["expires_at"]), tz=timezone.utc)
                            expires_at = exp_dt
                            token_status = "expired" if now >= exp_dt else "valid"
                except Exception:
                    token_status = "reauth_required"
            else:
                token_status = "reauth_required"

        res.append(
            AccountResponse(
                id=acct.id,
                connector_type=acct.connector_type,
                tag=acct.tag,
                identity_email=acct.identity_email,
                status=acct.status,
                is_default=acct.is_default,
                auth_type=auth_type,
                token_expires_at=expires_at,
                token_status=token_status,
            )
        )
    return res


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
    if body.db_schema is not None:
        project.schema = body.db_schema
    
    db.commit()
    db.refresh(project)

    try:
        import semabridge.api.main as main_module
        if hasattr(main_module, "_compat_projects") and project_id in main_module._compat_projects:
            main_module._compat_projects[project_id]["account_id"] = account.id
            main_module._compat_projects[project_id]["updated_at"] = main_module._compat_now_iso()
        from semabridge.api.services.project_config_store import get_project_config_text, save_project_config

        yaml_text = get_project_config_text(project_id)
        if yaml_text:
            import yaml
            parsed = yaml.safe_load(yaml_text) or {}
            if isinstance(parsed, dict):
                source_cfg = parsed.get("source") if isinstance(parsed.get("source"), dict) else {}
                source_cfg["identity_id"] = account.id
                parsed["source"] = source_cfg
                save_project_config(
                    project_id,
                    yaml.safe_dump(parsed, sort_keys=False, allow_unicode=False),
                    reason="link_account",
                )
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

    # Unlink all tables that reference this account before deleting it.
    # DuckDB enforces FK constraints at DELETE time, not at transaction commit,
    # so we must commit the unlink updates before issuing the DELETE — flush()
    # alone is not sufficient.
    try:
        db.execute(
            update(Project)
            .where(Project.account_id == account.id)
            .values(account_id=None)
        )
    except Exception:
        pass  # projects table may not have account_id column on older schemas

    # Also unlink from the in-memory _compat_projects store (JSON-backed).
    try:
        import semabridge.api.services.project_shared as _ps
        if hasattr(_ps, "_compat_projects"):
            for _proj in _ps._compat_projects.values():
                if isinstance(_proj, dict) and _proj.get("account_id") == account.id:
                    _proj["account_id"] = None
            if hasattr(_ps, "_compat_save_store"):
                _ps._compat_save_store()
    except Exception:
        pass

    # Commit the unlink first so DuckDB releases the FK reference before we delete.
    db.commit()

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


class AccountUpdate(BaseModel):
    tag: Optional[str] = None
    identity_email: Optional[str] = None
    credentials: Optional[dict] = None


@router.patch("/{account_id}", response_model=List[AccountResponse])
@router.put("/{account_id}", response_model=List[AccountResponse])
def update_account(
    request: Request,
    account_id: str,
    body: AccountUpdate,
    db: Session = Depends(get_db),
):
    """Edit account in-place (tag, identity, or credentials) and return updated list."""
    user = _try_get_user(request, db)
    account = db.execute(select(Account).where(Account.id == account_id)).scalar_one_or_none()

    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    if user and account.owner_id != user.id:
        raise HTTPException(status_code=403, detail="Account not found or access denied")

    if body.tag and body.tag != account.tag:
        stmt = select(Account).where(Account.tag == body.tag)
        if user:
            stmt = stmt.where(Account.owner_id == user.id)
        existing = db.execute(stmt).scalar_one_or_none()
        if existing and existing.id != account_id:
            raise HTTPException(status_code=400, detail=f"Tag '{body.tag}' is already in use.")
        account.tag = body.tag

    if body.identity_email is not None:
        account.identity_email = body.identity_email

    if body.credentials:
        import json

        clean_creds = _merge_credential_bundle(account.encrypted_token, body.credentials)
        account.encrypted_token = encrypt_token(json.dumps(clean_creds))
        if clean_creds.get("auth_type"):
            account.auth_type = clean_creds["auth_type"]

    db.commit()
    logger.info(f"Updated Account {account_id} ({account.tag})")
    return get_accounts(request=request, connector_type=account.connector_type, db=db)


@router.post("/{account_id}/test")
def test_account(request: Request, account_id: str, db: Session = Depends(get_db)):
    """Test connection for a specific account."""
    user = _try_get_user(request, db)
    stmt = select(Account).where(Account.id == account_id)
    if user:
        stmt = stmt.where(Account.owner_id == user.id)
    account = db.execute(stmt).scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="Account not found")

    connector = account.connector_type.upper()
    if connector == "FABRIC":
        try:
            from semabridge.auth.token_resolver import resolve_fabric_access_token

            token = resolve_fabric_access_token(None, identity_id=account.id)
            if not token:
                return {"status": "failed", "message": "Failed to resolve Fabric access token"}
            return {"status": "success", "message": "Fabric authentication successful"}
        except Exception as exc:
            return {"status": "failed", "message": f"Fabric authentication failed: {exc}"}
    elif connector == "SNOWFLAKE":
        try:
            import json
            import snowflake.connector
            from semabridge.auth.encryption import decrypt_token

            if not account.encrypted_token:
                return {"status": "failed", "message": "No credentials stored for Snowflake account"}
            creds = json.loads(decrypt_token(account.encrypted_token))
            conn_kwargs = {
                "account": creds.get("account"),
                "user": creds.get("user"),
                "warehouse": creds.get("warehouse"),
                "database": creds.get("database"),
                "schema": creds.get("schema_name"),
            }
            if creds.get("password"):
                conn_kwargs["password"] = creds["password"]
            elif creds.get("private_key"):
                from semabridge.connectors.snowflake_connection import _parse_private_key

                conn_kwargs["private_key"] = _parse_private_key(
                    creds["private_key"], creds.get("private_key_passphrase")
                )
            conn = snowflake.connector.connect(**conn_kwargs)
            conn.cursor().execute("SELECT CURRENT_VERSION()")
            conn.close()
            return {"status": "success", "message": "Snowflake connection successful"}
        except Exception as exc:
            return {"status": "failed", "message": f"Snowflake connection failed: {exc}"}
    return {"status": "failed", "message": f"Testing not supported for connector '{connector}'"}



