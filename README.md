# diagrun

Local CLI and agent tool that wraps C++ builds, stores the full compiler log,
and returns a compact failure record instead of the raw dump.

**Not an LLM proxy.** The model calls `diagrun_build` as a tool; the agent
runtime runs diagrun on this machine. See [docs/architecture.md](docs/architecture.md).

```bash
PYTHONPATH=src python3 -m diagrun -- make
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Docs: [docs/](docs/). Plan: [plan.md](plan.md).
