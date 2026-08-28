# Principles

## Product promise

Preserve the information needed to fix a failed C++ build. Collapse
cascades. Never silently drop unknowns.

diagrun is a **deterministic diagnostic compiler**, not a summarizer and not
a repair agent. It does not generate patches. The coding agent still reads
source and applies the fix.

## Failure information, not transcripts

A raw `cmake --build` dump is a transcript. The target representation is
independent root failures plus evidence plus a retrievable raw log:

```text
roots          independent faults the agent should act on
evidence       notes / duplicate TUs that justify a root
suppressed     build-system or cascade messages that add no new fault
unclassified   anything the reducer cannot prove is a consequence — keep it
raw.ref        full log, on demand
```

Unknowns stay visible. Prefer a false negative on collapse (leave a noisy
follow-on) over hiding a real fault. `--collapse-parse-recovery` is **off**
by default for that reason.

## Wrapper, not interceptor

- Humans: `diagrun -- make` looks like the original command (live streams,
  original exit code, no extra summary line).
- Agents: named tools (`diagrun_build`, …). The model must choose them.
  diagrun does not monkey-patch `bash`.

Prefer `diagrun_build` over `bash` for C++ compile/link. Do not paste the
full compiler log unless `diagrun_get_raw` is needed.

## Capture fidelity

- stdout and stderr are stored independently.
- Arrival order is recorded (`events.jsonl`) so interleaving can be rebuilt.
- Run ids are ULIDs; the store is local and GC'd.
- Injection only adds diagnostic formatting flags; it must not change
  codegen.

## Deterministic first

Parsers, fingerprints, and grouping rules come before any LLM reduction
step. Intelligence is added only where deterministic structure is
insufficient. Fixture corpora (`fixtures/cpp_failures/`) define expected
roots; the reducer is tested against those, not against model judgment.
