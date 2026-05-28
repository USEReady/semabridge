import subprocess
import os

def check_git(path):
    if not os.path.exists(path):
        print(f"Path does not exist: {path}")
        return
    print(f"=== Git status for {path} ===")
    try:
        branch = subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=path).decode().strip()
        print(f"Branch: {branch}")
        status = subprocess.check_output(["git", "status", "--porcelain"], cwd=path).decode().strip()
        print("Local changes:")
        print(status if status else "None")
        log = subprocess.check_output(["git", "log", "-n", "3", "--oneline"], cwd=path).decode().strip()
        print("Last 3 commits:")
        print(log)
    except Exception as e:
        print("Error:", e)
    print("-" * 50)

if __name__ == "__main__":
    check_git(r"c:\Users\MANOJ\semabridge-working")
    check_git(r"c:\Users\MANOJ\dev-test\semabridge")
