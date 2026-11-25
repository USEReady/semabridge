import ctypes
from ctypes import wintypes

def enum_all_windows():
    wins = []
    def callback(hwnd, extra):
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        length = ctypes.windll.user32.GetWindowTextLengthW(hwnd)
        buff = ctypes.create_unicode_buffer(length + 1)
        ctypes.windll.user32.GetWindowTextW(hwnd, buff, length + 1)
        visible = bool(ctypes.windll.user32.IsWindowVisible(hwnd))
        wins.append((pid.value, hwnd, visible, buff.value))
        return True

    EnumWindowsProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    ctypes.windll.user32.EnumWindows(EnumWindowsProc(callback), 0)
    return wins

for pid, hwnd, vis, title in enum_all_windows():
    if pid in [14040, 33852] or "power" in title.lower() or "competitive" in title.lower():
        print(f"PID {pid}: HWND {hwnd}, Visible: {vis}, Title: '{title}'")
