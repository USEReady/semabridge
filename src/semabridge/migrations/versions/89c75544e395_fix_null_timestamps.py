"""fix null timestamps

Revision ID: 89c75544e395
Revises: f2b8c9d1e0a4
Create Date: 2026-04-27 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '89c75544e395'
down_revision: Union[str, Sequence[str], None] = 'f2b8c9d1e0a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    \"\"\"Upgrade schema.\"\"\"
    # For snapshots: update NULL or epoch timestamps to now
    op.execute(\"\"\"
        UPDATE snapshots 
        SET timestamp = NOW() 
        WHERE timestamp IS NULL OR timestamp <= '1970-01-02';
    \"\"\")
    
    # For runs: update NULL or epoch started_at to now
    op.execute(\"\"\"
        UPDATE runs 
        SET started_at = NOW() 
        WHERE started_at IS NULL OR started_at <= '1970-01-02';
    \"\"\")
    
    # Ensure they are NOT NULL (though they should be already)
    with op.batch_alter_table('snapshots', schema=None) as batch_op:
        batch_op.alter_column('timestamp', nullable=False)

    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.alter_column('started_at', nullable=False)


def downgrade() -> None:
    \"\"\"Downgrade schema.\"\"\"
    # We don't want to revert the data updates, and the NOT NULL was already there
    pass
