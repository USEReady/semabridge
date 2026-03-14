"""
Sentinel Error Analyzer.

Parses error messages and inspects Snowflake metadata to determine
the root cause of compilation failures.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Any, List
from contextlib import contextmanager
from snowflake.connector import SnowflakeConnection

from semabridge.core.settings import SnowflakeConfig
from semabridge.core.sentinel.monitor import QueryFailure
from semabridge.utils.logger import get_logger
import snowflake.connector

logger = get_logger(__name__)


@dataclass
class ErrorContext:
    """Context for a specific error analysis."""
    failure: QueryFailure
    object_name: Optional[str] = None
    suggested_fix: Optional[str] = None
    root_cause: Optional[str] = None


class ErrorParser:
    """Parses Snowflake error messages to extract context."""

    # Regex patterns for common errors
    REGEX_904 = re.compile(r"invalid identifier '(.+?)'", re.IGNORECASE)
    REGEX_2003 = re.compile(r"Object '(.+?)' does not exist or not authorized", re.IGNORECASE)
    REGEX_2001 = re.compile(r"Table '(.+?)' does not exist", re.IGNORECASE)

    @staticmethod
    def parse(failure: QueryFailure) -> ErrorContext:
        """Parse the error message to extract the problematic object."""
        ctx = ErrorContext(failure=failure)
        msg = failure.error_message

        if failure.is_invalid_identifier:
            match = ErrorParser.REGEX_904.search(msg)
            if match:
                ctx.object_name = match.group(1)
        
        elif failure.is_object_not_found:
            match = ErrorParser.REGEX_2003.search(msg) or ErrorParser.REGEX_2001.search(msg)
            if match:
                ctx.object_name = match.group(1)
        
        return ctx


class IdentifierAnalyzer:
    """Analyzes 000904 (Invalid Identifier) errors."""
    
    def __init__(self, config: SnowflakeConfig):
        self.config = config

    @contextmanager
    def connection(self) -> SnowflakeConnection:
        conn = snowflake.connector.connect(
            user=self.config.user,
            password=self.config.password.get_secret_value(),
            account=self.config.account,
            warehouse=self.config.warehouse,
            database=self.config.database,
            schema=self.config.schema_name,
            role=self.config.role,
        )
        try:
            yield conn
        finally:
            conn.close()

    def analyze(self, ctx: ErrorContext) -> ErrorContext:
        """
        Check if the invalid identifier exists with different casing.
        
        Strategies:
        1. Check if it's a column in the current schema.
        2. Check unquoted vs quoted variations.
        """
        if not ctx.object_name:
            return ctx

        # Assume the identifier is a column for now (simplest case)
        # We need to know which table it belongs to.
        # This is hard without parsing the full query, but we can look for *any* match 
        # in the schema if the query context is vague.
        
        # Heuristic: if identifier has a dot, it might be table.column
        parts = ctx.object_name.split('.')
        col_name_candidate = parts[-1].strip('"')
        
        logger.info(f"Analyzing invalid identifier candidate: {col_name_candidate}")

        with self.connection() as conn:
            cur = conn.cursor()
            
            # Search for this column name in the entire schema, case-insensitive
            cur.execute(f"""
                SELECT TABLE_NAME, COLUMN_NAME 
                FROM INFORMATION_SCHEMA.COLUMNS 
                WHERE TABLE_SCHEMA = '{self.config.schema_name.upper()}'
                  AND (
                      COLUMN_NAME = '{col_name_candidate}' 
                      OR COLUMN_NAME ILIKE '{col_name_candidate}'
                  )
            """)
            
            matches = cur.fetchall()
            
            if matches:
                # We found a match!
                found_table, found_col = matches[0]
                
                # Check casing differences
                if found_col != col_name_candidate and found_col.upper() != col_name_candidate.upper():
                    # This shouldn't happen with ILIKE unless it's a fuzzy match, 
                    # but here we are checking exact or case-insensitive equal.
                    pass
                
                # If the error was because of quotes
                # e.g. defined as "REV FOR EXP", queried as 'REV FOR EXP' or "rev for exp"
                
                # If the found column needs quotes (has spaces or mixed case)
                needs_quotes = not re.match(r'^[A-Z0-9_]+$', found_col)
                fixed_name = f'"{found_col}"' if needs_quotes else found_col
                
                ctx.root_cause = f"Identifier found in table '{found_table}'."
                
                # Only suggest a deterministic fix if it's a casing/quoting fix.
                # If it's an exact match, the issue is likely the Table reference (wrong table),
                # so we should let the AI Agent handle the complex SQL rewrite.
                if found_col != col_name_candidate:
                     ctx.suggested_fix = fixed_name
                     ctx.root_cause += f" Case/quoting mismatch (Expected: {fixed_name})."
                else:
                     ctx.root_cause += " Name matches exactly. Likely wrong table reference."
                     # specific_fix = None -> Falls back to Agent
            else:
                ctx.root_cause = "Identifier not found in schema columns."
        
        return ctx


class AccessAnalyzer:
    """Analyzes 002003 (Object Not Found / Not Authorized) errors."""
    
    def __init__(self, config: SnowflakeConfig):
        self.config = config

    @contextmanager
    def connection(self) -> SnowflakeConnection:
        conn = snowflake.connector.connect(
            user=self.config.user,
            password=self.config.password.get_secret_value(),
            account=self.config.account,
            warehouse=self.config.warehouse,
            database=self.config.database,
            schema=self.config.schema_name,
            role=self.config.role,
        )
        try:
            yield conn
        finally:
            conn.close()

    def analyze(self, ctx: ErrorContext) -> ErrorContext:
        """
        Check if object exists and if role has access.
        
        1. Check if object exists (as a privileged role would see it? 
           We can only check as the current role).
        2. If we can't see it, we can't definitively say it exists but we don't have access,
           unless we are a superuser.
           
        For now, we assume the Sentinel runs with a role that *should* see things, 
        or we just check what privileges are missing if we know it *should* be there (e.g. from YAML).
        """
        if not ctx.object_name:
            return ctx
            
        object_name = ctx.object_name.upper().strip('"')
        
        # Check standard grants
        # Note: This is a simplified check. A full check requires traversing up from database -> schema -> table.
        
        with self.connection() as conn:
            cur = conn.cursor()
            
            # Check Database USAGE
            cur.execute(f"SHOW GRANTS ON DATABASE {self.config.database}")
            db_grants = [row[5] for row in cur.fetchall()] # grantee_name
            
            # Check Schema USAGE
            # Check Schema USAGE
            cur.execute(f"SHOW GRANTS ON SCHEMA {self.config.schema_name}")
            schema_grants = [row[5] for row in cur.fetchall()]
            
            role = self.config.role
            if not role:
                # If role is not configured, get the current role from the session
                cur.execute("SELECT CURRENT_ROLE()")
                role = cur.fetchone()[0]
            
            if role:
                role = role.upper()
                
                missing = []
                if role not in db_grants:
                    missing.append(f"GRANT USAGE ON DATABASE {self.config.database} TO ROLE {role};")
                
                if role not in schema_grants:
                    missing.append(f"GRANT USAGE ON SCHEMA {self.config.schema_name} TO ROLE {role};")
            else:
                missing = []
                logger.warning("Could not determine current role for permission analysis.")
                
            if missing:
                ctx.root_cause = "Missing USAGE privileges on parent objects."
                ctx.suggested_fix = "\n".join(missing)
            else:
                # If we have parent access, maybe the object is missing or we lack SELECT
                ctx.root_cause = "Parent access OK. Object might be missing or explicitly revoked."
                # We could try to generate a GRANT SELECT assuming it exists
                ctx.suggested_fix = f"GRANT SELECT ON TABLE {object_name} TO ROLE {role};"
                
        return ctx

    def check_permissions(self) -> List[str]:
        """
        Proactively check for common permission issues.
        Returns a list of warnings.
        """
        warnings = []
        role = self.config.role
        
        with self.connection() as conn:
            cur = conn.cursor()
            
            if not role:
                 cur.execute("SELECT CURRENT_ROLE()")
                 role = cur.fetchone()[0]
                 
            if not role:
                return ["Could not determine role to check permissions."]
                
            role = role.upper()
            
            # 1. Check Database Usage
            try:
                cur.execute(f"SHOW GRANTS ON DATABASE {self.config.database}")
                db_grants = [row[5] for row in cur.fetchall()]
                if role not in db_grants:
                    warnings.append(f"Role {role} lacks USAGE on DATABASE {self.config.database}")
            except Exception as e:
                warnings.append(f"Failed to check DATABASE grants: {e}")
                
            # 2. Check Schema Usage
            try:
                cur.execute(f"SHOW GRANTS ON SCHEMA {self.config.schema_name}")
                schema_grants = [row[5] for row in cur.fetchall()]
                if role not in schema_grants:
                    warnings.append(f"Role {role} lacks USAGE on SCHEMA {self.config.schema_name}")
            except Exception as e:
                warnings.append(f"Failed to check SCHEMA grants: {e}")
                
            # 3. Check Warehouse Usage
            try:
                cur.execute(f"SHOW GRANTS ON WAREHOUSE {self.config.warehouse}")
                wh_grants = [row[5] for row in cur.fetchall()]
                if role not in wh_grants:
                    warnings.append(f"Role {role} lacks USAGE on WAREHOUSE {self.config.warehouse}")
            except Exception as e:
                warnings.append(f"Failed to check WAREHOUSE grants: {e}")
                
        return warnings
