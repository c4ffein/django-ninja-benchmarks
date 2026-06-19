"""Route-style factorial for the parse/validate endpoint (c=1).

Group A  sync  : sync `def` view  on gunicorn (WSGI)   -> /api/create
Group B  async : async `def` view on uvicorn (ASGI)    -> /api/create_async

Each group holds route-style + server constant and varies only the framework,
so the number is (mostly) pure framework request-handling + validation overhead.
adrf provides the genuinely-async DRF cell. Flask's async view is async_to_sync
under a WsgiToAsgi shim (not ASGI-native) -- noted, not hidden.
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

GROUPS = {
    "sync (def / gunicorn WSGI)": {
        "endpoint": "/api/create",
        "frameworks": {
            "ninja": ("gunicorn -w 1 -b 127.0.0.1:{port} djninja.wsgi:application", "app_ninja"),
            "flask": ("gunicorn -w 1 -b 127.0.0.1:{port} main:app", "app_flask_marshmallow"),
            "drf":   ("gunicorn -w 1 -b 127.0.0.1:{port} drf.wsgi:application", "app_drf"),
        },
    },
    "async (async def / uvicorn ASGI)": {
        "endpoint": "/api/create_async",
        "frameworks": {
            "ninja": ("uvicorn djninja.asgi:application --host 127.0.0.1 --port {port} --workers 1 --log-level warning", "app_ninja"),
            "flask": ("uvicorn asgi:application --host 127.0.0.1 --port {port} --workers 1 --log-level warning", "app_flask_marshmallow"),
            "adrf":  ("uvicorn drf.asgi:application --host 127.0.0.1 --port {port} --workers 1 --log-level warning", "app_drf"),
        },
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
        raise RuntimeError(f"failed to bind: {tmpl}")
    return p


def stop(p):
    p.terminate()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill(); p.wait()
    wait_port(APP_PORT, up=False, timeout=10)
    time.sleep(0.3)


def oha_create(endpoint):
    cmd = [OHA, "-z", "3s", "-c", "1", "--no-tui", "--output-format", "json",
           "--disable-keepalive", "-m", "POST", "-D", os.path.join(BASE, "payload.json"),
           "-T", "application/json", f"http://127.0.0.1:{APP_PORT}{endpoint}"]
    out = subprocess.run(cmd, capture_output=True, text=True)
    d = json.loads(out.stdout)
    return round(d["summary"]["requestsPerSec"], 1), round(d["summary"]["successRate"], 3)


ROUNDS = int(os.environ.get("ROUNDS", "1"))  # >1 = repeat round-robin & average


def aggregate(samples):
    vals = [s[0] for s in samples]
    n = len(vals)
    mean = sum(vals) / n
    std = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5 if n > 1 else 0.0
    return {"rps": round(mean, 1), "rps_std": round(std, 1),
            "rps_min": min(vals), "rps_max": max(vals),
            "ok": round(min(s[1] for s in samples), 3),
            "n": n, "samples": [round(v, 1) for v in vals]}


def main():
    ns = subprocess.Popen([os.path.join(VENV, "python"), "network_service.py"],
                          cwd=BASE, env={**ENV, "PORT": str(NS_PORT)},
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wait_port(NS_PORT, up=True)
    # round-robin cells: (group, framework) interleaved, repeated ROUNDS times
    cells = [(gname, fw) for gname, g in GROUPS.items() for fw in g["frameworks"]]
    samples = {c: [] for c in cells}
    try:
        for rnd in range(1, ROUNDS + 1):
            for gname, fw in cells:
                tmpl, cwd = GROUPS[gname]["frameworks"][fw]
                p = start(tmpl, cwd)
                try:
                    oha_create(GROUPS[gname]["endpoint"])  # warmup
                    rps, ok = oha_create(GROUPS[gname]["endpoint"])
                finally:
                    stop(p)
                samples[(gname, fw)].append((rps, ok))
                print(f"  r{rnd:>2} {gname:34} {fw:6} -> {rps:>8} rps  (ok={ok})", flush=True)
    finally:
        ns.terminate()
        try:
            ns.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ns.kill()

    results = {}
    for (gname, fw), ss in samples.items():
        results.setdefault(gname, {})[fw] = aggregate(ss)
    results_dir = os.path.join(BASE, "benchmark_results")
    os.makedirs(results_dir, exist_ok=True)
    with open(os.path.join(results_dir, "results_route_matrix.json"), "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved -> benchmark_results/results_route_matrix.json  ({ROUNDS} round(s), mean)")


if __name__ == "__main__":
    main()
