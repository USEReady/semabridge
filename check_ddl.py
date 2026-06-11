import time
import os

for _ in range(30):
    if os.path.exists(r'C:\Users\MANOJ\dev-test\semabridge\ddl_debug.sql'):
        print("FOUND DDL!")
        break
    time.sleep(1)
else:
    print("NOT FOUND")
