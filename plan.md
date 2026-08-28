# Build Plan: Agent-Native C++ Diagnostic Reducer

## 1. Product goal

Build a command-execution layer for coding agents that turns failed C++ builds into a compact, structured explanation of the independent failures that matter.

Instead of returning the full compiler and build-system dump:

```text
agent
  ↓
cmake --build build
  ↓
80 KB compiler output
  ↓
agent context
```

the tool should return:

```text
agent
  ↓
diagnostic-aware build wrapper
  ↓
compiler/build execution
  ↓
diagnostic graph
  ↓
root failures + evidence + raw-log reference
```

The core product promise is:

> Preserve the information needed to fix a failed build while removing diagnostics that are only consequences of the same underlying fault.

The first version should target **C++ with GCC/Clang and CMake/Ninja/Make**. Do not start with arbitrary shell commands.

---

# 2. Product boundary

The tool is not a generic summarizer and should not try to generate patches initially.

Its responsibility is:

```text
failed build
    ↓
extract diagnostics
    ↓
normalize
    ↓
group and relate
    ↓
identify probable root diagnostics
    ↓
return compact structured result
```

The repair agent remains responsible for understanding the source code and applying a fix.

This keeps the component composable with Claude Code, Codex-style agents, SWE-agent, OpenHands, custom MCP agents, and future runtimes.

---

# 3. Initial user experience

The simplest interface should look like a transparent command wrapper.

```bash
diagrun cmake --build build -j8
```

On success, behave approximately like the normal command.

On failure, return a compact representation such as:

```text
BUILD FAILED

Root diagnostics: 2

[D1] include/widget.hpp:51:17
     no member named 'foo' in 'Widget'
     affected translation units: 7
     collapsed diagnostics: 23

     evidence:
       - same source location appears in 7 translation units
       - 14 template-instantiation errors reference Widget::foo

[D2] src/config.cpp:93:12
     use of undeclared identifier 'MAX_SIZE'
     collapsed diagnostics: 4

Build-system consequences suppressed: 11
Unclassified diagnostics retained: 2

Full output: diag://run/01JXYZ/raw
```

For agents, also support JSON:

```bash
diagrun --format json cmake --build build
```

---

# 4. Core output schema

Define the schema before implementing parsers. Everything downstream should target this representation.

```json
{
  "status": "failed",
  "command": "cmake --build build -j8",
  "exit_code": 2,
  "roots": [
    {
      "id": "D17",
      "severity": "error",
      "kind": "missing_member",
      "message": "no member named 'foo' in 'Widget'",
      "location": {
        "file": "include/widget.hpp",
        "line": 51,
        "column": 17
      },
      "symbol": "Widget::foo",
      "confidence": 0.97,
      "affected_translation_units": 7,
      "collapsed_diagnostics": 23,
      "evidence": [
        "same source location across translation units",
        "dependent template-instantiation diagnostics"
      ]
    }
  ],
  "unclassified": [],
  "suppressed": {
    "cascaded_diagnostics": 23,
    "build_system_messages": 11
  },
  "raw": {
    "ref": "diag://run/01JXYZ/raw",
    "bytes": 83142
  }
}
```

The schema should explicitly distinguish:

```text
root
dependent
build consequence
duplicate manifestation
unknown
```

Unknown diagnostics should be retained. The reducer should prefer false negatives over deleting potentially independent failures.

---

# 5. Architecture

Use a layered architecture so that parser logic, causal rules, and agent integration remain independent.

```text
                     ┌─────────────────────┐
                     │     Agent / CLI     │
                     └──────────┬──────────┘
                                │
                         command request
                                │
                     ┌──────────▼──────────┐
                     │ Execution wrapper   │
                     └──────────┬──────────┘
                                │
                  stdout/stderr │ exit status
                                │
                     ┌──────────▼──────────┐
                     │ Diagnostic parsers  │
                     │ GCC / Clang / Build │
                     └──────────┬──────────┘
                                │
                     normalized diagnostics
                                │
                     ┌──────────▼──────────┐
                     │ Diagnostic reducer  │
                     │ graph + rules       │
                     └──────────┬──────────┘
                                │
                     root diagnostic set
                                │
                ┌───────────────▼──────────────┐
                │ Renderer / JSON / MCP result │
                └──────────────────────────────┘

Raw output ───────────────────────────────→ local run store
```

Keep raw output out of the returned agent context but always retrievable.

---

# 6. Repository structure

A practical initial repository layout:

```text
diagrun/
├── cmd/
│   └── diagrun/
├── exec/
│   ├── runner
│   └── capture
├── diagnostics/
│   ├── model
│   ├── gcc
│   ├── clang
│   ├── ninja
│   ├── make
│   └── cmake
├── reducer/
│   ├── graph
│   ├── rules
│   ├── clustering
│   └── scoring
├── project/
│   ├── compile_commands
│   ├── includes
│   └── build_graph
├── store/
│   └── runs
├── render/
│   ├── text
│   └── json
├── integrations/
│   ├── mcp
│   └── shell
└── fixtures/
    └── cpp_failures
```

Implementation language should favor fast startup, easy distribution, and robust process handling. Rust or Go would both fit well. C++ itself is also viable if integration with compiler tooling becomes dominant.

---

# 7. Milestone 1 — Command wrapper and raw-log store

Build the execution substrate first.

Requirements:

* execute arbitrary build commands;
* capture stdout and stderr independently;
* preserve ordering information;
* return the original exit code;
* store complete raw output locally;
* generate a stable run ID;
* support retrieval by run ID;
* impose configurable storage limits and retention.

Example:

```bash
diagrun cmake --build build
diagrun show 01JXYZ --raw
```

At this point there is no reduction yet.

### Done when

The wrapper can replace a normal C++ build command without changing build semantics.

---

# 8. Milestone 2 — Normalize GCC and Clang diagnostics

The next step is converting compiler output into typed diagnostics.

Target normalized fields:

```text
severity
message
diagnostic kind/code when available
file
line
column
symbol when extractable
translation unit
compiler invocation
notes[]
template-instantiation frames[]
```

Preserve the relationship between an error and its associated `note:` entries.

For example:

```text
error
 ├── note: candidate function not viable
 ├── note: candidate template ignored
 └── note: in instantiation of ...
```

should become one diagnostic object with structured evidence rather than four independent messages.

### Parser strategy

Prefer, in order:

1. compiler-supported structured diagnostics when practical;
2. stable compiler diagnostic formatting;
3. textual fallback parsers.

Do not make regex parsing the architectural abstraction. Parsers should emit the same internal model regardless of input format.

### Diagnostic injection (default on)

`diagrun` may add **codegen-neutral** compiler flags so structured diagnostics are available. This must not change generated object code.

Default: inject. Opt out with `--no-inject-diagnostics` (or `DIAGRUN_INJECT_DIAGNOSTICS=0`).

| Tool | How flags are added |
|------|---------------------|
| raw `g++` / `clang++` | splice `-fdiagnostics-format=json` into argv |
| Make | wrap `CC`/`CXX` (do not replace makefile `CXXFLAGS`) |
| CMake configure | `CMAKE_C/CXX_COMPILER_LAUNCHER` |
| CMake `--build` / Ninja | PATH compiler shims + Clang `CCC_OVERRIDE_OPTIONS` |

CMake-generated Ninja files often use an absolute compiler path; injection then depends on configure-time `COMPILER_LAUNCHER`. Text fallback remains for logs produced without injection.

### Parse-recovery policy (default: keep uncertain errors)

Prefer false negatives: a likely follow-on syntax-recovery diagnostic stays visible unless the user opts in.

- default / `--no-collapse-parse-recovery`: keep D2 in `syntax_cascade`
- `--collapse-parse-recovery` or `DIAGRUN_COLLAPSE_PARSE_RECOVERY=1`: hide it when a rule is confident


### Done when

Representative GCC and Clang fixture logs produce stable JSON snapshots.

---

# 9. Milestone 3 — Remove obvious build-system noise

Implement high-confidence suppression before attempting causal reasoning.

Examples:

```text
ninja: build stopped: subcommand failed
make[2]: *** [...] Error 1
make[1]: *** [...] Error 2
make: *** [...] Error 2
```

These should become metadata:

```json
{
  "build_system_consequences": 4
}
```

rather than agent-visible diagnostics.

Also collapse exact duplicate compiler diagnostics emitted from repeated invocations where source location, diagnostic type, and message are equivalent.

### Done when

A failed build containing one compiler error plus layers of Make/Ninja propagation returns only the compiler diagnostic.

---

# 10. Milestone 4 — Cross-translation-unit clustering

This is the first important differentiator.

One bad header can generate the same logical failure in many translation units:

```text
foo.cpp → include/widget.hpp:51
bar.cpp → include/widget.hpp:51
baz.cpp → include/widget.hpp:51
```

Collapse these into one diagnostic cluster:

```text
root candidate:
  include/widget.hpp:51

manifestations:
  foo.cpp
  bar.cpp
  baz.cpp
```

Cluster initially using high-confidence keys:

* same source location;
* same compiler diagnostic class/code;
* normalized message equality;
* same referenced symbol;
* same originating header.

Avoid approximate semantic clustering in the first implementation.

### Done when

The tool turns repeated manifestations of the same header fault into one agent-visible item with an affected-translation-unit count.

---

# 11. Milestone 5 — Diagnostic dependency graph

Represent diagnostics internally as a graph.

```text
D1 ─────→ D2
 │
 ├──────→ D3
 │
 └──────→ D4
```

An edge means:

```text
D2 is probably a consequence or manifestation of D1
```

Start with deterministic edge rules.

### High-confidence edge types

#### Compiler-explicit relationships

Use:

```text
in instantiation of
required from
candidate ignored because
constraint not satisfied because
note
```

#### Build relationships

```text
compiler error
  → object target failed
  → library target failed
  → executable target failed
  → build command failed
```

#### Repeated source origin

Multiple diagnostics caused by the same source location or same invalid symbol can share a parent cluster.

#### Parse-recovery cascades

Certain compiler diagnostics can be marked as probable downstream syntax-recovery noise when emitted immediately after a structural parse error in the same region.

Every edge should carry:

```text
reason
confidence
rule identifier
```

This makes the output auditable.

---

# 12. Milestone 6 — Root selection

Given the graph, identify the minimal high-confidence set of root diagnostics.

The first implementation should not solve a complex global optimization problem. Use conservative graph rules:

1. diagnostics with no confident incoming dependency edges are roots;
2. descendants of a root are collapsed when edge confidence exceeds a threshold;
3. uncertain nodes remain visible;
4. build-system consequence nodes are always hidden unless no compiler/linker diagnostic exists;
5. cap the number of visible descendants but preserve a retrievable list.

Conceptually:

```text
visible = roots + unknowns + selected evidence
hidden  = confident descendants + duplicates + build consequences
```

### Done when

The reducer can explain why each hidden diagnostic was suppressed.

---

# 13. Milestone 7 — Project structure enrichment

Once log-only reduction works, add project-level evidence.

## compile_commands.json

Use it to map diagnostics to:

* translation units;
* compiler invocations;
* include paths;
* compile definitions.

## Include graph

Build a lightweight include graph when useful:

```text
widget.hpp
   ↓
service.hpp
   ↓
service.cpp
```

This helps distinguish one shared-header fault from independent translation-unit failures.

## Ninja/CMake target graph

Use build metadata to understand propagation across targets.

Do not require this information for the tool to function. Treat it as enrichment.

---

# 14. Milestone 8 — Agent integration

Support three integration modes.

## Shell wrapper

```bash
diagrun cmake --build build
```

Best for experimentation and drop-in usage.

## JSON subprocess API

```bash
diagrun --format json -- cmake --build build
```

Best for agent runtimes that already execute commands themselves.

## MCP tool

```text
diagrun mcp
```

Tools:

```text
diagrun_build(command, cwd?, inject_diagnostics?, collapse_parse_recovery?)
diagrun_get_raw(run_id?, offset?, limit?)
diagrun_show(run_id?)
diagrun_get_diagnostic(run_id, diagnostic_id)
```

JSON subprocess API for in-process plugins:

```bash
echo '{"command":"cmake --build build"}' | diagrun call build
```

## DeepSeek Harness plugin

Install the bundle:

```bash
dsh plugin --profile <name> add ./integrations/dsh-diagrun
```

Registers `diagrun_build`, `diagrun_get_raw`, `diagrun_show`, and `diagrun_get_diagnostic` via `defineTool`.

## Pi agent

Native extension (auto-loaded from this repo via `.pi/extensions/diagrun.ts`):

```bash
pi -e ./plugins/diagrun/dev.pi.agent/index.ts
```

Or Agent Plugins MCP (Pi `pi-mcp-adapter` `agentPluginPaths`):

```text
plugins/diagrun/   # plugin.json + mcp.json
```

The default `build` result is compact. Raw output only enters context when the agent calls `diagrun_get_raw`.

---

# 15. Retrieval model

Suppression is safe only if detail remains available.

Provide progressive disclosure:

```text
Level 0
root diagnostics only

Level 1
root + evidence

Level 2
root + collapsed diagnostic list

Level 3
selected raw-output ranges

Level 4
complete raw log
```

An agent should be able to request:

```text
show descendants of D17
show compiler notes for D17
show raw region around D17
show complete log
```

This is more robust than trying to choose the perfect context representation in one pass.

---

# 16. MVP scope

Keep the first shippable version intentionally narrow.

```text
Language:
  C++

Compilers:
  GCC
  Clang

Build systems:
  Ninja
  Make
  CMake-generated builds

Failures:
  compilation errors
  basic linker errors

Output:
  text
  JSON

Reduction:
  compiler note grouping
  build-system consequence removal
  duplicate removal
  cross-TU clustering
  deterministic dependency graph

Storage:
  local raw-log store
```

Explicitly defer:

* arbitrary runtime logs;
* test failures;
* distributed builds;
* automatic code patches;
* LLM-based summarization;
* learned causal models;
* cloud service infrastructure.

---

# 17. MVP implementation sequence

Build in this order:

```text
1. process execution + raw capture
2. normalized diagnostic schema
3. Clang parser
4. GCC parser
5. Make/Ninja consequence parser
6. exact diagnostic deduplication
7. compiler note attachment
8. cross-translation-unit clustering
9. deterministic dependency graph
10. root selection
11. compact text renderer
12. JSON renderer
13. raw-log retrieval
14. shell wrapper
15. MCP adapter
```

This order produces usable intermediate versions rather than requiring the whole architecture before testing.

---

# 18. Fixture suite

The tool needs a curated corpus of small reproducible C++ projects from the beginning.

Create one fixture per failure pattern:

```text
missing_include/
missing_member/
wrong_function_signature/
template_instantiation/
concept_failure/
syntax_cascade/
shared_header_many_tus/
multiple_independent_errors/
linker_undefined_symbol/
make_propagation/
ninja_propagation/
generated_header_missing/
```

Each fixture should contain:

```text
source tree
build command
expected raw failure
expected normalized diagnostics
expected root diagnostics
expected collapsed diagnostics
```

Snapshot testing will be valuable because compiler output changes can otherwise silently break parsing.

---

# 19. Correctness principles

Token reduction must never dominate correctness.

Apply these rules:

### Never silently drop unknown diagnostics

If the reducer cannot explain why a diagnostic is dependent, retain it.

### Preserve exact source locations

Do not replace concrete compiler evidence with prose.

### Preserve important compiler notes

Candidate overloads, template instantiation frames, and constraint explanations may be essential for repair.

### Expose suppression counts

The agent should know that information was collapsed.

### Keep raw output addressable

Every result must contain a stable reference to the complete output.

### Make reduction deterministic by default

The same build output should produce the same diagnostic representation.

---

# 20. Observability

Instrument the tool from the start.

For every run, record:

```text
raw bytes
raw estimated tokens
diagnostic count
root count
duplicate count
dependent count
unknown count
returned bytes
returned estimated tokens
reduction ratio
parser failures
rule hits
processing latency
```

This makes it possible to understand where reduction comes from and detect over-aggressive rules.

---

# 21. Validation strategy

The first product validation does not require a large research benchmark.

Use three layers.

## Unit validation

For each fixture, assert that:

* important root diagnostics remain;
* known cascades collapse;
* unrelated diagnostics remain independent;
* raw output is retrievable.

## Repository validation

Run against real open-source C++ repositories and compare:

```text
raw build output
vs
diagrun output
```

Inspect false suppressions first. They are much more dangerous than weak compression.

## Agent validation

Run the same agent with:

```text
full logs
vs
diagrun structured output
```

Track:

* build-fix success;
* build iterations;
* context tokens;
* raw-log retrieval frequency.

If agents frequently request the raw log, the reduced representation is missing information.

---

# 22. Primary product metrics

Track a small set of metrics that reflect the tool's real objective.

## Root retention

How often does the compact result preserve the diagnostic needed to fix the build?

This is the most important metric.

## Observation reduction

```text
raw build tokens / returned tokens
```

## Raw fallback rate

How often does an agent need to retrieve suppressed information?

Lower is better, provided repair success remains high.

## Repair success delta

```text
repair success with reducer
-
repair success with raw output
```

The ideal product outcome is:

```text
large token reduction
+
near-zero raw fallback
+
no repair-regression
```

---

# 23. When to introduce machine learning

Do not put an LLM in the critical path of the MVP.

Only consider learned inference after collecting failures where deterministic structure leaves meaningful ambiguity.

At that point, add a classifier for questions such as:

```text
Is D23 probably caused by D4?
```

Useful features could include:

* diagnostic type;
* shared symbol;
* shared source region;
* include relationship;
* template relationship;
* build dependency;
* temporal order;
* message embedding similarity.

The learned model should produce edges or confidence scores inside the same graph model. It should not replace the architecture with free-form summarization.

---

# 24. Optional LLM fallback

If later needed, use an LLM only on unresolved diagnostic clusters.

```text
full build log
      ↓
deterministic parsing
      ↓
high-confidence reduction
      ↓
small ambiguous cluster
      ↓
optional LLM
```

The LLM should receive structured diagnostics rather than the complete raw log.

This preserves the token-saving property of the system.

---

# 25. Distribution model

Start as a local open-source CLI.

Target installation should be approximately:

```bash
brew install diagrun
# or
cargo install diagrun
# or
download single binary
```

Avoid requiring a daemon, database, or cloud account for the first version.

The local-first model has useful properties:

* source code stays local;
* build logs stay local;
* integration is easy for agents;
* latency is low;
* deterministic functionality has no inference cost.

---

# 26. Integration strategy with agent runtimes

There are two useful adoption paths.

## Explicit usage

Teach the agent:

```text
Use `diagrun` for build commands.
```

This is easiest initially.

## Transparent interception

Later provide hooks that rewrite common commands:

```text
cmake --build ...
ninja ...
make ...
```

through the diagnostic layer automatically.

Transparent interception should come only after behavior is stable because command rewriting creates a larger compatibility surface.

---

# 27. Extension path after C++

Do not generalize until the C++ pipeline is robust.

The architecture should nevertheless expose a language-agnostic normalized model.

Likely next adapters:

```text
Rust
  cargo / rustc

TypeScript
  tsc

Java
  javac / Maven / Gradle

Go
  go build / go test
```

Each adapter should implement:

```text
raw/native diagnostics
      ↓
NormalizedDiagnostic[]
```

The reducer should then reuse as much generic logic as possible.

---

# 28. Suggested release sequence

## v0.1 — Structured compiler output

```text
GCC + Clang parsing
JSON diagnostics
raw-log storage
build-system noise removal
```

Useful but not yet deeply differentiated.

## v0.2 — Diagnostic collapsing

```text
note attachment
duplicate suppression
cross-TU clustering
shared-header grouping
```

This should already produce substantial token reduction.

## v0.3 — Causal reducer

```text
diagnostic graph
dependency rules
root selection
suppression explanations
```

This is the version where the core idea becomes visible.

## v0.4 — Agent integration

```text
MCP interface
progressive retrieval
agent-oriented result schema
metrics
```

## v0.5 — Project-aware reduction

```text
compile_commands.json
include graph
build graph
target-level propagation
```

## v1.0 — Stable agent diagnostic protocol

Stabilize:

* result schema;
* plugin/parser API;
* retrieval API;
* CLI behavior;
* compatibility guarantees.

---

# 29. First two-week implementation target

A strong initial target is a working prototype that proves the architecture.

Build only:

```text
command wrapper
raw output capture
GCC parser
Clang parser
normalized JSON
Make/Ninja propagation suppression
exact duplicate grouping
same-location cross-TU grouping
raw-log reference
```

Test it against a fixture corpus containing roughly 20–30 representative failures.

The expected output should already transform cases such as:

```text
74 compiler/build messages
```

into something like:

```text
2 independent diagnostics
31 collapsed manifestations
9 compiler notes retained as evidence
32 build-system/duplicate messages suppressed
```

without invoking an LLM.

---

# 30. Immediate engineering tasks

Start with these concrete tasks:

| # | Task | Status |
|---|------|--------|
| 1 | Define `Diagnostic`, `DiagnosticGroup`, `DependencyEdge`, and `BuildResult` types. | **Tested — passed** |
| 2 | Implement process capture with stable run IDs. | **Tested — passed** |
| 3 | Create the raw-output store and retrieval command. | **Tested — passed** |
| 4 | Build 10 minimal failing C++ fixtures. | **Ready for Testing** |
| 5 | Implement Clang diagnostic parsing. | Open |
| 6 | Implement GCC diagnostic parsing. | Open |
| 7 | Attach compiler notes to parent errors. | Open |
| 8 | Parse and suppress Make/Ninja consequence messages. | Open |
| 9 | Add exact diagnostic fingerprinting. | Open |
| 10 | Add same-location/same-symbol cross-TU grouping. | Open |
| 11 | Render compact text and JSON. | Open |
| 12 | Instrument token/byte reduction metrics. | Open |
| 13 | Run the prototype on several real C++ repositories. | Open |
| 14 | Record cases where the reducer returns too much or hides too much. | Open |
| 15 | Use those failures to design the first dependency-graph rules. | Open |

Task 1 implementation: `src/diagrun/diagnostics/model.py`. Tests: `tests/test_model.py`, `tests/test_task1_model.py`.

Tasks 2–3 implementation: `src/diagrun/exec/`, `src/diagrun/store/runs.py`, `src/diagrun/cli.py`, `src/diagrun/ids.py`. Tests: `tests/test_capture_store.py`, `tests/test_ids.py`, `tests/test_task2_3_4.py`.

```bash
PYTHONPATH=src python3 -m diagrun [--store DIR] [--] COMMAND...
PYTHONPATH=src python3 -m diagrun show [--raw] RUN_ID
PYTHONPATH=src python3 -m diagrun show [--raw] --last
```

Store default: `$DIAGRUN_STORE` or `$XDG_DATA_HOME/diagrun`. Retention: `DIAGRUN_MAX_RUNS` (100), `DIAGRUN_MAX_BYTES` (512MiB). Capture uses pipes + `select` (independent stdout/stderr, sequenced `events.jsonl`). Child sees `isatty()==False`.

Task 4: `fixtures/cpp_failures/` (10 GCC/Make fixtures; no Clang/Ninja/CMake on this host). Tests: `tests/test_fixtures.py`, `tests/test_task2_3_4.py`. Linker root `D1` now has `location` from `src/main.cpp:4:5`.

Diagnostic injection + reducer policy: `src/diagrun/config.py`, `src/diagrun/exec/inject.py`, `src/diagrun/reducer/policy.py`. Tests: `tests/test_inject_config.py`. **Ready for Testing**.

```bash
diagrun [--inject-diagnostics|--no-inject-diagnostics]
        [--collapse-parse-recovery|--no-collapse-parse-recovery]
        [--] COMMAND...
```

`PYTHONPATH=src python3 -m unittest discover -s tests -v`

### Tester log — Task 1 (2026-08-28)

Retest after `_validate_ids` included member ids: `test_unclassified_id_must_not_collide_with_member_id` now **passes**. Task 1 model tests: **all passed**.

### Tester log — Tasks 2–4 (2026-08-28)

`PYTHONPATH=src python3 -m unittest discover -s tests -v` → **128 passed, 1 failed**.

- Task 2 (capture + ULIDs): **passed** (independent stdout/stderr, interleaving, exit codes 0/3/126/127, `isatty()==False`, monotonic ULIDs).
- Task 3 (store + CLI): **passed** (raw retrieval, last pointer, GC by count/bytes, `show --raw` / `--last`, `--store=`).
- Task 4 (fixtures): **failed** `test_roots_have_concrete_locations`

**Failure:** `fixtures/cpp_failures/linker_undefined_symbol/expected/roots.json` root `D1` has no `location`. Captured raw includes `main.cpp:(.text+0x8): undefined reference to \`never_defined()'\`. Plan §19 requires preserving exact source locations.

Fix applied: `location` is `src/main.cpp:4:5` (call site of `never_defined()`). Re-mark Task 4 **Ready for Testing**.

The key implementation principle is:

> Build a deterministic diagnostic compiler first; add intelligence only where deterministic structure proves insufficient.

That path gives you a useful tool early while preserving the deeper opportunity: turning build output from a textual transcript into a compact machine-oriented representation of failure state.
