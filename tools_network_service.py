"""The benchmark rig's fake upstream: a fixed-latency stand-in for a slow I/O call.

Every route just `await asyncio.sleep(0.1)` then returns OK -- modelling a slow
downstream dependency. The concurrency panel's apps call this via
NETWORK_SERVICE_URL, and the 0.1s sleep is what imposes the ~10 req/s-per-connection
ceiling described in the README.

You normally never start this yourself -- `cli.py bench` spawns it (and reaps it)
around the runs that need it. It's exposed as `cli.py netsvc` only for poking the
apps by hand. Listens on $PORT (default 8000).
"""
import asyncio
import os
from sanic import Sanic
from sanic.response import text

app = Sanic("some_job")


@app.route("<path:path>")
async def test(request, path):
    await asyncio.sleep(0.1)  # This is the work
    return text('OK')


def main():
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), workers=2)


if __name__ == "__main__":
    main()
