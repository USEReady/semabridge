import sys
import re

def refactor_emitter(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    new_lines = []
    skip_until = -1
    
    for i, line in enumerate(lines):
        line_num = i + 1
        
        # 1. Update Imports
        if 'from semabridge.connectors.measure_sync import MeasureSynchronizer' in line:
            new_lines.append(line)
            new_lines.append('from semabridge.connectors.connection_manager import SnowflakeConnectionManager\n')
            continue
            
        # 2. Update __init__
        if 'def __init__(' in line:
            new_lines.append(line)
            # Find the end of __init__ or where to inject
            j = i + 1
            while j < len(lines) and 'self.measure_synchronizer = MeasureSynchronizer(' not in lines[j]:
                # Remove _connection, _session_conn, _verified_tables from __init__
                if 'self._connection = None' in lines[j]: pass
                elif 'self._session_conn = None' in lines[j]: pass
                elif 'self._verified_tables: set[str] = set()' in lines[j]: pass
                else:
                    new_lines.append(lines[j])
                j += 1
            
            if j < len(lines):
                # We found measure_synchronizer, inject connection_manager after it or near it
                # Let's finish measure_synchronizer first
                while j < len(lines) and '        )' not in lines[j]:
                    new_lines.append(lines[j])
                    j += 1
                if j < len(lines):
                    new_lines.append(lines[j]) # )
                    new_lines.append('        self.connection_manager = SnowflakeConnectionManager(\n')
                    new_lines.append('            config=self.config,\n')
                    new_lines.append('            behavior=self.behavior,\n')
                    new_lines.append('        )\n')
                    skip_until = j + 1
            continue

        if line_num <= skip_until:
            continue

        # 3. Remove migrated methods
        if line_num == 1240: # authenticate
            skip_until = 1246
            continue
        if line_num == 1247: # discover
            skip_until = 1259
            continue
        if line_num == 1261: # validate_permissions
            skip_until = 1285
            continue
        if line_num == 1292: # validate_target
            skip_until = 1298
            continue
        if line_num == 1305: # _execute_with_retry
            skip_until = 1384
            continue
        if line_num == 1387: # _ddl_preview
            skip_until = 1393
            continue
        if line_num == 1396: # _extract_invalid_identifier
            skip_until = 1403
            continue
        if line_num == 1442: # _execute_sql
            skip_until = 1454
            continue
        if line_num == 1461: # _resolve_warehouse
            skip_until = 1475
            continue
        
        # 4. Inject Proxy Methods (Inject where open_session was)
        if line_num == 1476:
            new_lines.append('    # -----------------------------------------------------------------\n')
            new_lines.append('    # Proxies for isolated execution domain\n')
            new_lines.append('    # -----------------------------------------------------------------\n')
            new_lines.append('    def _execute_sql(self, cursor, sql: str, params: Optional[tuple[Any, ...]] = None, *, context: str = "SQL") -> Any:\n')
            new_lines.append('        return self.connection_manager._execute_sql(cursor, sql, params=params, context=context)\n\n')
            new_lines.append('    def _execute_with_retry(self, cursor, sql: str, *, max_retries: int = 3, base_delay: float = 2.0, retryable_codes: tuple = ()) -> Any:\n')
            new_lines.append('        return self.connection_manager._execute_with_retry(cursor, sql, max_retries=max_retries, base_delay=base_delay, retryable_codes=retryable_codes)\n\n')
            new_lines.append('    def open_session(self, operation: str = "default") -> None:\n')
            new_lines.append('        self.connection_manager.open_session(operation)\n\n')
            new_lines.append('    def close_session(self) -> None:\n')
            new_lines.append('        self.connection_manager.close_session()\n\n')
            new_lines.append('    def authenticate(self) -> None:\n')
            new_lines.append('        self.connection_manager.authenticate()\n\n')
            new_lines.append('    def validate_target(self) -> bool:\n')
            new_lines.append('        return self.connection_manager.validate_target()\n\n')
            new_lines.append('    def validate_permissions(self) -> List[str]:\n')
            new_lines.append('        return self.connection_manager.validate_permissions()\n\n')
            new_lines.append('    def discover(self) -> Dict[str, Any]:\n')
            new_lines.append('        return self.connection_manager.discover()\n\n')
            new_lines.append('    def _ddl_preview(self, sql: str, *, max_lines: int = 30) -> str:\n')
            new_lines.append('        return self.connection_manager._ddl_preview(sql, max_lines=max_lines)\n\n')
            new_lines.append('    def _extract_invalid_identifier(self, exc: Exception) -> Optional[str]:\n')
            new_lines.append('        return self.connection_manager._extract_invalid_identifier(exc)\n\n')
            
            skip_until = 1509 # open_session + close_session
            continue

        # 5. Update deploy() connection strategy (1659-1673)
        if line_num == 1659:
            new_lines.append('            # Decide connection strategy: session vs per-call\n')
            new_lines.append('            conn, owns_conn = self.connection_manager.get_connection()\n')
            skip_until = 1673
            continue

        # 6. Update deploy_from_osi() connection strategy (2322-2335)
        if line_num == 2322:
            new_lines.append('            # Decide connection strategy: session vs per-call\n')
            new_lines.append('            conn, owns_conn = self.connection_manager.get_connection()\n')
            skip_until = 2335
            continue

        new_lines.append(line)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)

if __name__ == "__main__":
    refactor_emitter(sys.argv[1])
