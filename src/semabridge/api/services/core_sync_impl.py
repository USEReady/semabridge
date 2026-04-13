from semabridge.api.services.core_shared import *


async def sync_models(payload: Dict[str, Any]):
    """Trigger a full synchronization based on the current configuration.

    Resolves the project's linked ``account_id`` from the request payload
    or from the ORM ``Project.account_id`` relationship. This ensures that
    multi-user credential isolation is enforced during execution.
    """
    try:
        # Resolve account_id for multi-user credential scoping.
        # Priority: explicit payload > ORM project relationship.
        account_id = payload.get("account_id")

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

        return execute_sync_request(payload, _normalize_yaml_windows_path_fields, account_id=account_id)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Sync execution failed")
        raise HTTPException(status_code=500, detail=str(e))
