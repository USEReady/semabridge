import os
import filecmp

def compare_dirs():
    dir1 = r"c:\Users\MANOJ\semabridge-working\src"
    dir2 = r"c:\Users\MANOJ\dev-test\semabridge\src"
    
    if not os.path.exists(dir1):
        print(f"{dir1} does not exist!")
        return
    if not os.path.exists(dir2):
        print(f"{dir2} does not exist!")
        return
        
    print(f"Comparing {dir1} with {dir2}...")
    
    def compare_recursive(d1, d2):
        comparison = filecmp.dircmp(d1, d2)
        
        # Files in d1 but not in d2
        for f in comparison.left_only:
            print(f"[ONLY IN WORKING] {os.path.join(d1, f)}")
            
        # Files in d2 but not in d1
        for f in comparison.right_only:
            print(f"[ONLY IN DEV-TEST] {os.path.join(d2, f)}")
            
        # Diff in common files
        for f in comparison.diff_files:
            print(f"[DIFFERENT] {os.path.join(d1, f)} vs {os.path.join(d2, f)}")
            
        for sub in comparison.common_dirs:
            compare_recursive(os.path.join(d1, sub), os.path.join(d2, sub))

    compare_recursive(dir1, dir2)

if __name__ == "__main__":
    compare_dirs()
