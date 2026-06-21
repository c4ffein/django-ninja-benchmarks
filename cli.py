#!/usr/bin/env python
"""Single front door for the benchmark suite -- dispatches to the standalone scripts.

This is a thin, faithful pass-through: each tool's own argparse parses the
remaining args, and its exit code propagates unchanged (so `cli.py check` still
gates CI). The scripts below remain directly runnable on their own -- cli.py just
makes the whole workflow discoverable from one place (`cli.py --help`).

    cli.py bench local|server-matrix|route-matrix|kill   # the HTTP experiments (-> tools_bench.py)
    cli.py microbench ninja|drf|marshmallow              # validation CPU only, no HTTP
    cli.py charts [--ninja/--flask/--drf/--adrf COLOR]   # render the SVG charts
    cli.py check --dir DIR [--baseline-dir DIR]          # gate results against invariants
    cli.py netsvc                                        # the rig's fake upstream (bench starts it for you)

Each tool forwards --help, so e.g. `cli.py bench --help` shows bench's own usage.
"""
import os
import runpy
import sys

BASE = os.path.dirname(os.path.abspath(__file__))

# tool name -> (script filename, one-line help)
TOOLS = {
    "bench":      ("tools_bench.py", "HTTP experiments: local / server-matrix / route-matrix / kill"),
    "microbench": ("tools_microbench.py", "validation CPU only, no HTTP (one framework per process)"),
    "charts":     ("tools_charts.py", "render the parse/validate + concurrency SVG charts"),
    "check":      ("tools_check.py", "gate results_*.json against structural invariants"),
    "netsvc":     ("tools_network_service.py", "the rig's fixed-latency fake upstream (bench auto-starts it)"),
}


def usage(stream=sys.stdout):
    print(__doc__.strip(), file=stream)
    print("\ntools:", file=stream)
    for name, (_, help_) in TOOLS.items():
        print(f"  {name:<11} {help_}", file=stream)


def main():
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        usage()
        return 0
    tool, rest = argv[0], argv[1:]
    if tool not in TOOLS:
        print(f"unknown tool {tool!r}\n", file=sys.stderr)
        usage(sys.stderr)
        return 2
    script = os.path.join(BASE, TOOLS[tool][0])
    # Run the target script as if invoked as `python <script> <rest>`: its argv[0]
    # is the script, its argparse sees `rest`, and any SystemExit it raises
    # propagates out of run_path -> our exit code matches the tool's.
    sys.argv = [script, *rest]
    runpy.run_path(script, run_name="__main__")
    return 0


if __name__ == "__main__":
    sys.exit(main())
