from semabridge.repository.orm.session_factory import db_manager
from semabridge.repository.orm.models import Run
from sqlalchemy import delete

session = db_manager._session()
try:
    session.execute(delete(Run))
    session.commit()
    print("Success")
except Exception as e:
    session.rollback()
    print("Failed:", type(e).__name__, e)
finally:
    session.close()
