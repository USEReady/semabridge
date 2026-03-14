
import snowflake.connector
import os
from dotenv import load_dotenv

def verify_sync():
    load_dotenv()
    
    account = os.getenv("SNOWFLAKE_ACCOUNT")
    user = os.getenv("SNOWFLAKE_USER")
    password = os.getenv("SNOWFLAKE_PASSWORD")
    warehouse = os.getenv("SNOWFLAKE_WAREHOUSE")
    database = os.getenv("SNOWFLAKE_DATABASE")
    schema = os.getenv("SNOWFLAKE_SCHEMA")
    
    print(f"Connecting to Snowflake: {account} as {user}...", flush=True)
    
    try:
        conn = snowflake.connector.connect(
            user=user,
            password=password,
            account=account,
            warehouse=warehouse,
            database=database,
            schema=schema
        )
        cur = conn.cursor()
        
        # 1. Check Tables
        tables = ["FACT", "BU", "CALENDAR", "SCENARIO", "PRODUCT", "CUSTOMER", "INDUSTRY", "EXECUTIVE", "STATE"]
        print("\nChecking Source Tables:", flush=True)
        for table in tables:
            try:
                cur.execute(f"SELECT COUNT(*) FROM {table}")
                count = cur.fetchone()[0]
                print(f"  [OK] Table {table}: {count} rows", flush=True)
            except Exception as e:
                print(f"  [FAIL] Table {table}: {e}", flush=True)

        # 2. Check Semantic View
        view_name = "Model_1cb616cc_52b7_4268_b458_b092d64c84a6_semantic"
        print(f"\nChecking Semantic View: {view_name}", flush=True)
        try:
            # Check if view exists using SHOW SEMANTIC VIEWS
            cur.execute(f"SHOW SEMANTIC VIEWS LIKE '{view_name}'")
            res = cur.fetchone()
            if res:
                print(f"  [OK] Semantic View {view_name} exists.", flush=True)
            else:
                print(f"  [FAIL] Semantic View {view_name} NOT found.", flush=True)
                
            # Try to describe it
            print(f"  Testing description of {view_name}...", flush=True)
            cur.execute(f"DESCRIBE SEMANTIC VIEW {view_name}")
            print(f"  [OK] Semantic View description successful.", flush=True)
            
        except Exception as e:
            print(f"  [FAIL] Semantic View {view_name}: {e}", flush=True)


        conn.close()
    except Exception as e:
        print(f"CONNECTION ERROR: {e}")

if __name__ == "__main__":
    verify_sync()
