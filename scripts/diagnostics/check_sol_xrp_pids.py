import psutil
for pid in [18300, 20572]:
    try:
        p = psutil.Process(pid)
        cmd = ' '.join(p.cmdline() or [])
        print(f'PID {pid}: {cmd[:200]}')
    except Exception as e:
        print(f'PID {pid}: NOT FOUND ({e})')
