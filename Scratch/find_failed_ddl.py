import os

def find_failed_ddl():
    paths_to_search = [
        r"c:\Users\MANOJ\dev-test\semabridge",
        r"c:\Users\MANOJ\semabridge-working",
        r"c:\Users\MANOJ"
    ]
    
    for path in paths_to_search:
        if os.path.exists(path):
            print(f"Searching in {path}...")
            for root, dirs, files in os.walk(path):
                # Don't recurse too deep in unrelated folders
                if ".git" in root or ".venv" in root or ".pytest_cache" in root or "AppData" in root:
                    continue
                for file in files:
                    if "failed" in file.lower() or "ddl" in file.lower():
                        full_path = os.path.join(root, file)
                        print(f"Found match: {full_path} (size: {os.path.getsize(full_path)} bytes)")

if __name__ == "__main__":
    find_failed_ddl()
