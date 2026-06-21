"""Isolate validation CPU cost, no HTTP/server involved.

Runs the *actual* schema from each app against payload.json, N times, in-process.
Run one framework per process (Django settings can only be configured once):
    python tools_microbench.py ninja
    python tools_microbench.py drf
    python tools_microbench.py marshmallow
"""

import json
import os
import sys
import time

BASE = os.path.dirname(os.path.abspath(__file__))
fw = sys.argv[1]
N = int(os.environ.get("N", "50000"))
payload = json.load(open(os.path.join(BASE, "payload.json")))
sys.path.insert(0, BASE)

if fw == "ninja":
    sys.path.insert(0, os.path.join(BASE, "apps", "app_ninja"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "djninja.settings")
    import django

    django.setup()
    from djninja.api import Model  # ninja.Schema == pydantic v2 BaseModel

    def run():
        return Model.model_validate(payload)

elif fw == "drf":
    sys.path.insert(0, os.path.join(BASE, "apps", "app_drf"))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "drf.settings")
    import django

    django.setup()
    from drf.views import Model

    def run():
        s = Model(data=payload)
        s.is_valid()
        return s.validated_data

elif fw == "marshmallow":
    sys.path.insert(0, os.path.join(BASE, "apps", "app_flask_marshmallow"))
    from main import model_schema

    def run():
        return model_schema.load(payload)

else:
    raise SystemExit(f"unknown framework {fw}")

# warm up (JIT-free, but lets caches/first-call setup settle)
for _ in range(2000):
    run()

t0 = time.perf_counter()
for _ in range(N):
    run()
dt = time.perf_counter() - t0
print(json.dumps({"fw": fw, "N": N, "us_per_op": round(dt / N * 1e6, 2), "ops_per_s": round(N / dt)}))
