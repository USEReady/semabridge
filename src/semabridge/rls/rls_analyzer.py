"""
RLS Analyzer for Fabric Semantic Models.

Parses TMSL (Tabular Model Scripting Language) definitions to extract
Role-Level Security roles and filters.
"""

import json
from typing import List, Dict, Any
from semabridge.models.phase_3_enterprise import RLSPolicy
from semabridge.utils.logger import get_logger

logger = get_logger(__name__)

class RlsAnalyzer:
    """
    Analyzes Fabric RLS definitions and extracts security policies.
    """
    
    def analyze_tmsl(self, tmsl_json: str) -> List[RLSPolicy]:
        """
        Parses TMSL JSON and returns a list of RLSPolicy objects.
        """
        policies = []
        try:
            data = json.loads(tmsl_json)
            roles = data.get("model", {}).get("roles", [])
            
            for role in roles:
                role_name = role.get("name")
                table_permissions = role.get("tablePermissions", [])
                
                for perm in table_permissions:
                    table_name = perm.get("name")
                    filter_expr = perm.get("filterExpression")
                    
                    if filter_expr:
                        policy = RLSPolicy(
                            id=f"rls_{role_name}_{table_name}".lower(),
                            role=role_name,
                            table=table_name,
                            filter_expression=filter_expr,
                            policy_type="row_filter"
                        )
                        policies.append(policy)
                        logger.info(f"Extracted RLS: Role={role_name}, Table={table_name}")
                        
        except Exception as e:
            logger.error(f"Failed to parse TMSL RLS: {e}")
            
        return policies

    def extract_from_metadata(self, metadata: Dict[str, Any]) -> List[RLSPolicy]:
        """
        Extracts RLS from internal Semabridge metadata structure.
        """
        # Placeholder for metadata-based extraction
        return []
