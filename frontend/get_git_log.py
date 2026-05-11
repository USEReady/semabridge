import subprocess
import sys

try:
    result = subprocess.run(['git', 'log', '-p', '-n', '2', 'frontend/src/pages/ProjectConfigPage.jsx'], 
                            cwd=r'c:\Users\MANOJ\semabridge-working',
                            capture_output=True, text=True, check=True)
    with open(r'c:\Users\MANOJ\semabridge-working\frontend\git_log_output.txt', 'w', encoding='utf-8') as f:
        f.write(result.stdout)
    print("Success")
except Exception as e:
    print(f"Error: {e}")
