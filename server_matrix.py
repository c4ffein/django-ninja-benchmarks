"""Parse/validate panel as a server x framework factorial.

Answers: is the validation ranking server-independent? Runs /api/create at c=1
for every (framework, server) cell:
    sync  = uWSGI (WSGI)
    async = uvicorn (ASGI; Flask via WsgiToAsgi shim)
"""
import json
import os
import socket
import subprocess
import time

BASE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(BASE, ".venv", "bin")
OHA = os.path.expanduser("~/.local/bin/oha")
NS_PORT, APP_PORT = 9000, 8000
ENV = {**os.environ, "PYTHONPATH": BASE, "NETWORK_SERVICE_URL": f"http://127.0.0.1:{NS_PORT}/job"}

# framework -> server -> (cmd template, cwd)
MATRIX = {
    "ninja": {
        "sync":  ("uwsgi --http 127.0.0.1:{port} --module djninja.wsgi:application --workers 1 --master --need-app --die-on-term", "app_ninja"),
        "async": ("uvicorn djninja.asgi:application --host 127.0.0.1 --port {port} --workers 1 --log-level warning", "app_ninja"),
    },
    "flask": {
        "sync":  ("uwsgi --http 127.0.0.1:{port} --module main:app --workers 1 --master --need-app --die-on-term", "app_flask_marshmallow"),
        "async": ("uvicorn asgi:application --host 127.0.0.1 --port {port} --workers 1 --log-level warning", "app_flask_marshmallow"),
    },
    "drf": {
        "sync":  ("uwsgi --http 127.0.0.1:{port} --module drf.wsgi:application --workers 1 --master --need-app --die-on-term", "app_drf"),
        "async": ("uvicorn drf.asgi:application --host 127.0.0.1 --port {port} --workers 1 --log-level warning", "app_drf"),
    },
}


def wait_port(port, up=True, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                if up:
                    return True
        except OSError:
            if not up:
                return True
        time.sleep(0.2)
    return up is False


def start(tmpl, cwd):
    cmd = [os.path.join(VENV, p) if i == 0 else p
           for i, p in enumerate(tmpl.format(port=APP_PORT).split())]
    p = subprocess.Popen(cmd, cwd=os.path.join(BASE, cwd), env=ENV,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_port(APP_PORT, up=True):
        p.terminate()
        raise RuntimeError("server failed to bind")
    return p


def stop(p):
    p.terminate()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill(); p.wait()
    wait_port(APP_PORT, up=False, timeout=10)
    time.sleep(0.3)


def oha_create():
    cmd = [OHA, "-z", "3s", "-c", "1", "--no-tui", "--output-format", "json",
           "--disable-keepalive", "-m", "POST", "-D", os.path.join(BASE, "payload.json"),
           "-T", "application/json", f"http://127.0.0.1:{APP_PORT}/api/create"]
    d = json.loads(subprocess.run(cmd, capture_output=True, text=True).stdout)
    return round(d["summary"]["requestsPerSec"], 1)


def main():
    ns = subprocess.Popen([os.path.join(VENV, "python"), "network_service.py"],
                          cwd=BASE, env={**ENV, "PORT": str(NS_PORT)},
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_port(NS_PORT, up=True)
    results = {}
    try:
        for fw in ("ninja", "flask", "drf"):
            results[fw] = {}
            for server in ("sync", "async"):
                tmpl, cwd = MATRIX[fw][server]
                p = start(tmpl, cwd)
                try:
                    oha_create()  # warmup
                    rps = oha_create()
                finally:
                    stop(p)
                results[fw][server] = rps
                print(f"  {fw:6} {server:5} ({'uWSGI' if server=='sync' else 'uvicorn'}) -> {rps:>8} rps", flush=True)
    finally:
        ns.terminate()
        try:
            ns.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ns.kill()

    results_dir = os.path.join(BASE, "benchmark_results")
    os.makedirs(results_dir, exist_ok=True)
    with open(os.path.join(results_dir, "results_server_matrix.json"), "w") as f:
        json.dump(results, f, indent=2)
    print("\n# parse/validate rps  (c=1)        all-sync   all-async")
    for fw in ("ninja", "flask", "drf"):
        print(f"  {fw:8} : {results[fw]['sync']:>10} {results[fw]['async']:>11}")
    print("\nSaved -> benchmark_results/results_server_matrix.json")


if __name__ == "__main__":
    main()
