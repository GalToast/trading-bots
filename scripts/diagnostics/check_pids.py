import psutil
for pid in [14436, 4892, 44888, 40300, 44368, 35336, 44492, 21224]:
    try:
        p = psutil.Process(pid)
        cmd = ' '.join(p.cmdline() or [])
        print(f'PID {pid} ({p.memory_info().rss // 1024}KB): {cmd[:150]}')
    except:
        print(f'PID {pid}: NOT FOUND')
