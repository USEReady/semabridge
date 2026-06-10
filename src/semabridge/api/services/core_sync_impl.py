from semabridge.api.services.core_shared import *
from semabridge.domain.exceptions import InternalError, NotFoundError, PermissionError, SemaBridgeError


async def sync_models(payload: Dict[str, Any]):
    """Trigger a full synchronization based on the current configuration.

    Resolves the project's linked ``account_id`` from the request payload
    or from the ORM ``Project.account_id`` relationship. This ensures that
    multi-user credential isolation is enforced during execution.

    When ``AUTH_ENABLED=true``, an additional ownership check verifies that
    the resolved account belongs to the requesting user (``user_id`` from
    the JWT, injected by AuthMiddleware into ``request.state``).
    """
    try:
        # Resolve account_id for multi-user credential scoping.
        # Priority: explicit payload > ORM project relationship.
        account_id = payload.get("account_id")
        user_id = payload.get("user_id")  # Injected by controller when auth is enabled

        if not account_id:
            project_id = payload.get("project_id")
            if project_id:
                try:
                    from semabridge.repository.orm.models import Project
                    from sqlalchemy import select
                    from semabridge.repository.orm.session_factory import db_manager

                    session = db_manager.get_session_factory()()
                    try:
                        project = session.execute(
                            select(Project).where(Project.project_id == project_id)
                        ).scalar_one_or_none()
                        if project and project.account_id:
                            account_id = project.account_id
                    finally:
                        session.close()
                except Exception as acct_err:
                    logger.debug("Could not resolve account_id for project %s: %s", project_id, acct_err)

        # Phase 2C: Ownership validation — prevent User A from using User B's Account.
        if account_id and user_id and os.environ.get("AUTH_ENABLED", "").lower() == "true":
            _validate_account_ownership(account_id, int(user_id))

        if user_id and os.environ.get("AUTH_ENABLED", "").lower() == "true":
            from semabridge.auth.user_credentials import scoped_user_env

            with scoped_user_env(int(user_id), "api_secrets"):
                return execute_sync_request(
                    payload,
                    _normalize_yaml_windows_path_fields,
                    account_id=account_id,
                )

        return execute_sync_request(payload, _normalize_yaml_windows_path_fields, account_id=account_id)
    except SemaBridgeError:
        raise
    except Exception as e:
        logger.exception("Sync execution failed")
        raise InternalError(str(e))


def _validate_account_ownership(account_id: str, user_id: int) -> None:
    """Verify that the account belongs to the requesting user.

    This is a critical multi-tenant security check. Without this, User A
    could craft a sync request referencing User B's account_id and execute
    with User B's credentials.

    Args:
        account_id: The Account.id to validate.
        user_id: The authenticated User.id from the JWT.

    Raises:
        HTTPException 403: If the account does not belong to the user.
    """
    from sqlalchemy import select
    from semabridge.repository.orm.models import Account
    from semabridge.repository.orm.session_factory import db_manager

    try:
        with db_manager.get_session() as session:
            account = session.execute(
                select(Account).where(Account.id == account_id)
            ).scalar_one_or_none()

            if not account:
                raise NotFoundError(f"Account '{account_id}' not found.")

            if account.owner_id is not None and account.owner_id != user_id:
                logger.warning(
                    "Ownership violation: user %d attempted sync with account %s (owner=%s)",
                    user_id, account_id, account.owner_id,
                )
                raise PermissionError("Account access denied — this account belongs to another user.")
    except SemaBridgeError:
        raise
    except Exception as exc:
        logger.error("Account ownership check failed: %s", exc)
        # Fail-open only in development — fail-closed in production
        if os.environ.get("AUTH_ENABLED", "").lower() == "true":
            raise InternalError("Account ownership validation failed.") from exc
