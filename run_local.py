"""No-Docker, no-root runner (2026 modernization).

Same methodology as run_test.py, but runs natively instead of via docker-compose:
  - sync apps (DRF, Flask)  -> uWSGI prefork workers (one request per worker, like the original)
  - Ninja (async)           -> uvicorn workers (identical to the original)
  - network_service (sanic) -> started locally on PORT=9000
  - load generator          -> oha (multi-threaded) instead of ApacheBench

Runs BOTH panels of the original chart:
  c1        : parsing/validation JSON  (POST /api/create, concurrency=1, 1 worker)
  concurrent: slow network operation   (GET  /api/iojob,  concurrency=50, worker sweep)

Usage:  uv run python run_local.py     (or: source .venv/bin/activate && python run_local.py)
"""
import json
import os
import socket
import subprocess
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
VENV = os.path.join(BASE, ".venv", "bin")
OHA = os.path.expanduser("~/.local/bin/oha")
NS_PORT = 9000
APP_PORT = 8000
ENV = {**os.environ,
       "PYTHONPATH": BASE,
       "NETWORK_SERVICE_URL": f"http://127.0.0.1:{NS_PORT}/job"}

WORKERS_CASES = [1, 2, 4, 6, 8, 10, 12, 14, 16, 18, 20, 22, 24]  # matches the chart x-axis

# (argv, cwd) ; {w} replaced with worker count. uWSGI for sync, uvicorn for async.
FRAMEWORKS = {
    "flask": ("uwsgi --http 127.0.0.1:{port} --module main:app --workers {w} --master --need-app --die-on-term",
              "app_flask_marshmallow"),
    "drf":   ("uwsgi --http 127.0.0.1:{port} --module drf.wsgi:application --workers {w} --master --need-app --die-on-term",
              "app_drf"),
    "ninja": ("uvicorn djninja.asgi:application --host 127.0.0.1 --port {port} --workers {w} --log-level warning",
              "app_ninja"),
}

# Parse/validate panel: all frameworks on the SAME (sync/WSGI) server so the
# number reflects framework+validation overhead, not the server model.
# (The original benchmark used `ninja_uwsgi` here for exactly this reason.)
C1_FRAMEWORKS = {
    "ninja": ("uwsgi --http 127.0.0.1:{port} --module djninja.wsgi:application --workers {w} --master --need-app --die-on-term",
              "app_ninja"),
    "flask": FRAMEWORKS["flask"],
    "drf":   FRAMEWORKS["drf"],
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


def start(name, workers, table=FRAMEWORKS):
    tmpl, cwd = table[name]
    cmd = [os.path.join(VENV, p) if i == 0 else p
           for i, p in enumerate(tmpl.format(port=APP_PORT, w=workers).split())]
    p = subprocess.Popen(cmd, cwd=os.path.join(BASE, cwd), env=ENV,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_port(APP_PORT, up=True):
        p.terminate()
        raise RuntimeError(f"{name} workers={workers} failed to bind :{APP_PORT}")
    return p


def stop(p):
    p.terminate()
    try:
        p.wait(timeout=10)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
    wait_port(APP_PORT, up=False, timeout=10)
    time.sleep(0.3)


def oha(url, concurrency, duration, payload=None):
    cmd = [OHA, "-z", f"{duration}s", "-c", str(concurrency),
           "--no-tui", "--output-format", "json", "--disable-keepalive"]
    if payload:
        cmd += ["-m", "POST", "-D", os.path.join(BASE, payload), "-T", "application/json"]
    cmd += [url]
    out = subprocess.run(cmd, capture_output=True, text=True)
    d = json.loads(out.stdout)
    return {
        "rps": round(d["summary"]["requestsPerSec"], 1),
        "p50": round(d["latencyPercentiles"]["p50"] * 1000, 1),
        "p99": round(d["latencyPercentiles"]["p99"] * 1000, 1),
        "ok": round(d["summary"]["successRate"], 3),
    }


FRAMEWORK_NAMES = ("ninja", "flask", "drf")
ROUNDS = int(os.environ.get("ROUNDS", "1"))          # >1 = "slow" mode: repeat & average
DURATION = float(os.environ.get("DURATION", "5"))    # concurrency panel load seconds
DURATION_C1 = float(os.environ.get("DURATION_C1", "3"))


def warm(endpoint, payload=None):
    oha(f"http://127.0.0.1:{APP_PORT}{endpoint}", 1, 1, payload)


def measure_cell(panel, fw, workers):
    """Start the server for one cell, warm it, take a single sample, stop it."""
    if panel == "c1":
        p = start(fw, workers, C1_FRAMEWORKS)
        try:
            warm("/api/create", "payload.json")
            return oha(f"http://127.0.0.1:{APP_PORT}/api/create", 1, DURATION_C1, "payload.json")
        finally:
            stop(p)
    else:
        p = start(fw, workers, FRAMEWORKS)
        try:
            warm("/api/iojob")
            return oha(f"http://127.0.0.1:{APP_PORT}/api/iojob", 50, DURATION)
        finally:
            stop(p)


def build_cells():
    """Round-robin order: frameworks interleaved (a/b/c/a/b/c), NOT grouped (a/a/b/b).
    A transient spike from another process is then spread across frameworks, not
    charged to whichever one happened to be running at the time."""
    cells = [("c1", fw, 1) for fw in FRAMEWORK_NAMES]
    for w in WORKERS_CASES:                       # same worker count measured back-to-back
        for fw in FRAMEWORK_NAMES:                # across the three frameworks
            cells.append(("conc", fw, w))
    return cells


def aggregate(samples):
    vals = [s["rps"] for s in samples]
    n = len(vals)
    mean = sum(vals) / n
    std = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5 if n > 1 else 0.0
    p50s = sorted(s["p50"] for s in samples)
    return {"rps": round(mean, 1), "rps_std": round(std, 1),
            "rps_min": min(vals), "rps_max": max(vals),
            "p50": p50s[n // 2], "ok": round(min(s["ok"] for s in samples), 3),
            "n": n, "samples": [round(v, 1) for v in vals]}


def main():
    ns = subprocess.Popen([os.path.join(VENV, "python"), "network_service.py"],
                          cwd=BASE, env={**ENV, "PORT": str(NS_PORT)},
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not wait_port(NS_PORT, up=True):
        raise RuntimeError("network_service failed to start")

    cells = build_cells()
    samples = {c: [] for c in cells}
    print(f"== {ROUNDS} round(s), round-robin order, {len(cells)} cells/round ==", flush=True)
    try:
        for rnd in range(1, ROUNDS + 1):
            for panel, fw, w in cells:
                r = measure_cell(panel, fw, w)
                samples[(panel, fw, w)].append(r)
                tag = "c1  create" if panel == "c1" else f"c50 w={w:<2}"
                print(f"  r{rnd:>2} {tag} {fw:6} -> {r['rps']:>8} rps (ok={r['ok']})", flush=True)
    finally:
        ns.terminate()
        try:
            ns.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ns.kill()

    c1 = {}
    conc = {fw: {} for fw in FRAMEWORK_NAMES}
    for (panel, fw, w), ss in samples.items():
        (c1 if panel == "c1" else conc[fw]).__setitem__(fw if panel == "c1" else w, aggregate(ss))

    results = {"meta": {"date": os.environ.get("RUN_DATE", "unknown"),
                        "load_tool": "oha", "sync_server": "uwsgi", "async_server": "uvicorn",
                        "c1_server": "uwsgi (all frameworks, fair)",
                        "rounds": ROUNDS, "duration_s": DURATION,
                        "order": "round-robin (framework-interleaved), mean of rounds"},
               "workers_cases": WORKERS_CASES, "c1": c1, "concurrent": conc}
    results_dir = os.path.join(BASE, "benchmark_results")
    os.makedirs(results_dir, exist_ok=True)
    with open(os.path.join(results_dir, "results_local.json"), "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n# mean rps by worker count over {ROUNDS} round(s) (slow network op, concurrency=50)")
    print("Framework  :" + "".join(f"{w:>9}" for w in WORKERS_CASES))
    for name in FRAMEWORK_NAMES:
        print(f"{name:<10} :" + "".join(f"{conc[name][w]['rps']:>9.0f}" for w in WORKERS_CASES))
    if ROUNDS > 1:
        print("std        :" + "  (per-cell rps_std in results_local.json)")
    print(f"\nparse/validate (c=1): " + ", ".join(f"{fw} {c1[fw]['rps']}±{c1[fw]['rps_std']}" for fw in FRAMEWORK_NAMES))
    print("Saved -> benchmark_results/results_local.json")


if __name__ == "__main__":
    main()
