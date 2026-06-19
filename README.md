# django-ninja-benchmarks

## Results (2026)

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="charts/parse_validate-dark.svg">
  <img alt="Parsing / validation JSON — concurrency 1, sync vs async" src="charts/parse_validate.svg">
</picture>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="charts/concurrency-dark.svg">
  <img alt="Calling a slow network operation — concurrency 50, worker sweep" src="charts/concurrency.svg">
</picture>

<sub>Re-run on a modern 2026 stack (this fork) — see [2026 local run](#2026-local-run-no-docker-no-root) below. Original 2020 result: [img/results.png](img/results.png).</sub>

---

### Requirements

 - Python3
 - Docker + docker-compose
 - ab

###

Run

```
python run_test.py
```

---

## 2026 local run (no Docker, no root)

The original suite is Docker + `ab` + uWSGI on a 2020 stack. This branch adds a
self-contained way to run it natively with [uv](https://docs.astral.sh/uv/), a modern
2026 stack, and [`oha`](https://github.com/hatoo/oha) instead of `ab` — no root needed
(uv's managed Python ships headers, so even uWSGI compiles).

```bash
uv venv --python-preference only-managed --python 3.14
uv pip install -r requirements-local.txt
cargo install oha            # or grab a prebuilt binary from the oha releases

uv run python run_local.py   # both panels -> benchmark_results/results_local.json / .csv
uv run python make_charts.py # -> charts/{parse_validate,concurrency}{,-dark}.svg (light + dark)
```

What changed vs the original apps: the network-service URL is read from
`NETWORK_SERVICE_URL` (so apps run outside Docker), `network_service.py` honors `PORT`,
and each app gained an `async def` `/api/create_async` variant (Flask via `asgi.py`,
DRF via `adrf`) for the route-style comparison below.

### Runners / experiments

| script | what it measures |
|---|---|
| `run_local.py` | both original panels, native (sync apps on uWSGI, Ninja async on uvicorn) |
| `microbench_validate.py` | validation CPU only, no HTTP (pydantic vs DRF vs marshmallow) |
| `server_matrix.py` | parse/validate per framework x {uWSGI, uvicorn} — the server confound |
| `route_matrix.py` | parse/validate: sync `def`/gunicorn vs async `def`/uvicorn, incl. `adrf` |
| `make_charts.py` | renders each chart as light + dark SVG (colors overridable: `--ninja/--flask/--drf/--adrf`) |

### Findings (this machine, 2026-06-14, 8 cores)

- **Concurrency panel reproduces the original**: Ninja's async views saturate at 1
  worker (~385-478 rps, flat); sync Flask/DRF scale ~linearly with workers (19->~230 at 24).
  The sync curves are nearly identical — the whole gap is the async model, not the framework.
- **Validation CPU (isolated)**: pydantic-2 (Ninja) **27 us** << marshmallow **95 us** <<
  DRF serializers **588 us** — pydantic-2 is ~21x faster than DRF.
- **But at the HTTP layer that's mostly hidden**: at c=1, validation is <2% of request time;
  per-request stack/server overhead dominates. Held to one server, Ninja wins parse/validate.
- **Route-style factorial**: a sync `def` route is ~2.5x faster than an `async def` route on
  this CPU-bound endpoint (async overhead with no I/O to overlap), for every framework.
  Ninja leads in both stacks; `adrf` is the slowest cell.

Results are committed under `benchmark_results/` (`results_local.json/.csv`,
`results_server_matrix.json`, `results_route_matrix.json`) so the numbers live somewhere
reproducible, not just in a PNG.
