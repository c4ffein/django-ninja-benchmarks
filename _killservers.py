import os, time
PAT = ('uwsgi', 'uvicorn', 'gunicorn', 'network_service', 'multiprocessing.spawn')
me = os.getpid()
for _ in range(4):
    left = []
    for pid in [d for d in os.listdir('/proc') if d.isdigit()]:
        if pid == str(me):
            continue
        try:
            cmd = open(f'/proc/{pid}/cmdline').read()
        except Exception:
            continue
        if any(p in cmd for p in PAT):
            left.append(pid)
            try:
                os.kill(int(pid), 9)
            except Exception:
                pass
    if not left:
        break
    time.sleep(1)
