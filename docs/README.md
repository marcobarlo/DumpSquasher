# diagrun

diagrun wraps C++ builds, stores the full compiler log, and returns a compact
failure record to a coding agent instead of dumping tens of kilobytes into
model context.

It is **not an HTTP proxy** in front of the LLM. The model calls it as a
**tool**. See [Architecture](architecture.md).

| Doc | Topic |
|-----|--------|
| [Architecture](architecture.md) | Layers, tool-call path, store, integrations |
| [Principles](principles.md) | What it must preserve and what it may drop |
| [Demo](demo.md) | Manual CLI, Pi, and DeepSeek Harness runs |

Python package: `src/diagrun/`. Tests: `tests/`. Fixtures: `fixtures/cpp_failures/`.
Product plan (including open parser work): [`../plan.md`](../plan.md).
