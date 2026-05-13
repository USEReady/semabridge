
import sys
import os

# Add src to path
sys.path.append(os.path.join(os.getcwd(), 'src'))

from semabridge.utils.synonyms import merge_synonyms, generate_auto_synonyms
from semabridge.connectors.synonym_clause import synonyms_clause

def smoke_test():
    print("--- Synonym Utilities Smoke Test ---")
    
    # 1. Test Auto Generation
    name = "SaleAmount"
    auto = generate_auto_synonyms(name)
    print(f"Name: {name} -> Auto: {auto}")
    
    # 2. Test Merge
    user = ["Revenue"]
    merged = merge_synonyms(user, auto)
    print(f"User: {user} + Auto -> Merged: {merged}")
    
    # 3. Test DDL Clause
    clause = synonyms_clause(merged)
    print(f"Clause: '{clause}'")
    
    # 4. Test Single Quote Escaping
    special = ["O'Brien's Sales"]
    clause_special = synonyms_clause(special)
    print(f"Special Name: {special} -> Clause: '{clause_special}'")
    
    # 5. Verify Priority
    user2 = ["Takings"]
    auto2 = ["Takings", "Income", "Revenue", "Money"]
    merged2 = merge_synonyms(user2, auto2)
    # User Takings should be first, auto-Income and auto-Revenue should follow (max 3 auto, but Income and Revenue are added)
    # Deduplication should handle Takings in auto.
    print(f"Priority Test: User {user2} + Auto {auto2} -> Merged: {merged2}")

if __name__ == "__main__":
    smoke_test()
