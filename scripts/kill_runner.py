import os
import psutil
import sys

def kill_process_by_cmdline(substring):
    for proc in psutil.process_iter(['pid', 'cmdline']):
        try:
            cmdline = proc.info['cmdline']
            if cmdline and any(substring in arg for arg in cmdline):
                print(f"Killing PID {proc.info['pid']}: {' '.join(cmdline)}")
                proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python kill_runner.py <substring>")
    else:
        kill_process_by_cmdline(sys.argv[1])
