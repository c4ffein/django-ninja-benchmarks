"""Shared benchmark harness: process lifecycle, load generation, aggregation.

Used by tools_bench.py (local / server-matrix / route-matrix experiments). Everything
that used to be copy-pasted across the three runner scripts lives here once.

tools_microbench.py deliberately does NOT use this: it measures validation
CPU in-process with no server/HTTP, so none of the plumbing below applies.
"""

import json
import os
import signal
import socket
import subprocess
import time
from contextlib import contextmanager

BASE = os.path.dirname(os.path.abspath(__file__))
# Overridable so the same harness runs under a local uv venv and inside Docker.
VENV = os.environ.get("BENCH_VENV_BIN") or os.path.join(BASE, ".venv", "bin")
OHA = os.environ.get("BENCH_OHA") or os.path.expanduser("~/.local/bin/oha")  # or tools_bench.py --oha


def env_for(ns_port):
    """Process env shared by app servers and the load run (points apps at the network service)."""
    return {**os.environ, "PYTHONPATH": BASE, "NETWORK_SERVICE_URL": f"http://127.0.0.1:{ns_port}/job"}


def wait_port(port, up=True, timeout=30):
    """Block until 127.0.0.1:port starts accepting (up=True) or stops (up=False)."""
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


def _resolve(template, port):
    """Split a command template, prefixing the executable (token 0) with the venv bin dir."""
    return [os.path.join(VENV, tok) if i == 0 else tok for i, tok in enumerate(template.format(port=port).split())]


class Server:
    """Context manager: start an app server, wait for it to bind, terminate on exit.

    Replaces the start()/try/finally/stop() ritual the three scripts each carried.
    """

    def __init__(self, template, cwd, *, app_port, env):
        self.template, self.cwd, self.app_port, self.env = template, cwd, app_port, env
        self.proc = None

    def __enter__(self):
        self.proc = subprocess.Popen(
            _resolve(self.template, self.app_port),
            cwd=os.path.join(BASE, self.cwd),
            env=self.env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if not wait_port(self.app_port, up=True):
            self.proc.terminate()
            raise RuntimeError(f"failed to bind :{self.app_port} -> {self.template}")
        return self

    def __exit__(self, *exc):
        p = self.proc
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()
        wait_port(self.app_port, up=False, timeout=10)
        time.sleep(0.3)
        return False


@contextmanager
def network_service(ns_port, env):
    """Context manager: run tools_network_service.py on ns_port for the enclosed block."""
    proc = subprocess.Popen(
        [os.path.join(VENV, "python"), "tools_network_service.py"],
        cwd=BASE,
        env={**env, "PORT": str(ns_port)},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if not wait_port(ns_port, up=True):
        proc.terminate()
        raise RuntimeError("network_service failed to start")
    try:
        yield proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def oha(url, concurrency, duration, payload=None, oha_bin=None):
    """Run one oha load sample, return {rps, p50(ms), p99(ms), ok}."""
    cmd = [
        oha_bin or OHA,
        "-z",
        f"{duration}s",
        "-c",
        str(concurrency),
        "--no-tui",
        "--output-format",
        "json",
        "--disable-keepalive",
    ]
    if payload:
        cmd += ["-m", "POST", "-D", os.path.join(BASE, payload), "-T", "application/json"]
    cmd += [url]
    d = json.loads(subprocess.run(cmd, capture_output=True, text=True).stdout)
    return {
        "rps": round(d["summary"]["requestsPerSec"], 1),
        "p50": round(d["latencyPercentiles"]["p50"] * 1000, 1),
        "p99": round(d["latencyPercentiles"]["p99"] * 1000, 1),
        "ok": round(d["summary"]["successRate"], 3),
    }


def aggregate(samples, *, include_p50=True):
    """Reduce repeated oha() samples (list of dicts) to mean/std/min/max + provenance.

    include_p50=False reproduces the route-matrix shape (which never recorded p50).
    """
    vals = [s["rps"] for s in samples]
    n = len(vals)
    mean = sum(vals) / n
    std = (sum((v - mean) ** 2 for v in vals) / n) ** 0.5 if n > 1 else 0.0
    out = {"rps": round(mean, 1), "rps_std": round(std, 1), "rps_min": min(vals), "rps_max": max(vals)}
    if include_p50:
        p50s = sorted(s["p50"] for s in samples)
        out["p50"] = p50s[n // 2]
    out["ok"] = round(min(s["ok"] for s in samples), 3)
    out["n"] = n
    out["samples"] = [round(v, 1) for v in vals]
    return out


def save_results(name, obj, output_dir=None):
    """Write obj as benchmark_results/<name>.json (indent=2). Returns the path."""
    output_dir = output_dir or os.path.join(BASE, "benchmark_results")
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, f"{name}.json")
    with open(path, "w") as f:
        json.dump(obj, f, indent=2)
    return path


# ---------------------------------------------------------------------------
# stray-process cleanup  (recover from a hard-killed / crashed run, where the
# Server/network_service context managers never got to terminate their child)
# ---------------------------------------------------------------------------


def _socket_inodes_on_ports(ports):
    """Inodes of TCP sockets (v4+v6) whose local port is in `ports`."""
    wanted, inodes = {int(p) for p in ports}, set()
    for path in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(path) as f:
                next(f, None)  # header row
                for line in f:
                    parts = line.split()
                    if len(parts) < 10:
                        continue
                    try:
                        port = int(parts[1].rsplit(":", 1)[1], 16)
                    except (ValueError, IndexError):
                        continue
                    if port in wanted:
                        inodes.add(parts[9])
        except OSError:
            pass
    return inodes


def _pids_owning_inodes(inodes):
    """PIDs holding an fd to any socket inode in `inodes` (catches worker children too)."""
    pids = set()
    if not inodes:
        return pids
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        fddir = f"/proc/{pid}/fd"
        try:
            for fd in os.listdir(fddir):
                try:
                    link = os.readlink(os.path.join(fddir, fd))
                except OSError:
                    continue
                if link.startswith("socket:[") and link[8:-1] in inodes:
                    pids.add(int(pid))
                    break
        except OSError:
            continue
    return pids


def _pids_matching(signatures):
    """PIDs whose cmdline contains any of the bench-specific `signatures`."""
    pids = set()
    if not signatures:
        return pids
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                cmd = f.read().replace(b"\x00", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if any(sig in cmd for sig in signatures):
            pids.add(int(pid))
    return pids


def kill_strays(ports=(), signatures=(), grace=2.0):
    """Reap orphaned bench servers left behind by an interrupted / crashed run.

    Deliberately scoped so it never touches unrelated servers: a PID is reaped
    only if it owns a socket on one of `ports` (the bench's app / network-service
    ports) OR its cmdline contains one of the bench-specific `signatures` (this
    repo's app modules / tools_network_service.py). SIGTERM, wait `grace`s, then
    SIGKILL whatever is still alive. Returns {"terminated": [...], "killed": [...]}.
    """
    pids = _pids_owning_inodes(_socket_inodes_on_ports(ports)) | _pids_matching(signatures)
    pids -= {os.getpid(), os.getppid()}
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    if pids:
        time.sleep(grace)
    killed = []
    for pid in pids:
        try:
            os.kill(pid, 0)  # still alive?
        except OSError:
            continue
        killed.append(pid)
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    return {"terminated": sorted(pids), "killed": sorted(killed)}
