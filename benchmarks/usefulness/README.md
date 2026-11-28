# Usefulness evaluation

This directory separates **mechanical rule evaluation** from **agent usefulness**.

Running the tests proves the guard behaves as implemented. It does not prove the
guard prevents useful regressions or that its review cost is acceptable.

## Mutation benchmark

`cases.json` is a labeled corpus of:

- positive cases that must produce a specific error/warning rule;
- clean cases that must remain `allow`;
- regression cases for known false positives and false negatives;
- C/C++ cases for header API, export macros, C test assertions, Unity tests,
  test deletion, include-direction rules, templates, and overload sets.

Run:

```sh
python benchmarks/usefulness/run.py --output /tmp/usefulness.json
```

The runner creates a temporary Git repository per case, commits the base, applies
the change, runs `aed check --json`, and reports case-level precision/recall and
false-block rate:

```text
cases=32 pass=32 tp=23 fn=0 fp=0 tn=9 precision=1.0 recall=1.0 false_block_rate=0.0
```

CI runs the benchmark with `--gate`; the gate requires recall ≥ 0.9 and a clean
case false-block rate ≤ 0.05. This is a regression gate for the benchmark, not a
claim about real-world precision.

### Case schema

```json
{
  "name": "test_assertion_removed",
  "expect": "block",
  "rules": ["test-tampering"],
  "base": {"tests/test.py": "def test_a():\n    assert 1 == 1\n"},
  "changes": [{"op": "write", "path": "tests/test.py", "content": "def test_a():\n    pass\n"}]
}
```

Changes support `write`, `delete`, `rename`, and `write_many`.

## Agent A/B protocol

A mutation corpus is necessary but not sufficient. Use a task set with the same
model, prompts, tools, time budget, and repository, and compare two arms:

- **baseline**: normal agent workflow;
- **guarded**: same workflow plus `aed check --staged` before commit.

Run 20–40 tasks, including feature work, refactors, test changes, API changes,
and a migration or irreversible decision. Blind-grade each result.

Write one JSONL record per task and arm:

```json
{"task":"T-001","success":true,"escaped_regressions":0,"findings":2,"review_minutes":3.5,"iterations":2}
```

`agent_ab.py` summarizes the two files:

```sh
python benchmarks/usefulness/agent_ab.py \
  --baseline baseline.jsonl \
  --guarded guarded.jsonl \
  --output /tmp/agent-ab.json
```

Metrics:

- success rate;
- escaped test/API regressions;
- findings per task;
- human review minutes;
- agent iterations.

A credible result needs a held-out task set and enough tasks to make the
difference meaningful. A perfect score on this mutation corpus alone is not
evidence that the guard is useful in production.

## Trial log

See [`trial-2026-11-27.md`](trial-2026-11-27.md) for the four-task A/B pilot.
It found behavioral policy differences but no hidden-test difference; it is not
large enough for a usefulness claim.

## Real-world history

The audit in [`docs/real-world-audit.md`](../../docs/real-world-audit.md) is a
separate real-world regression study. It measures diagnostic volume and runtime,
not labeled precision; do not equate finding count with usefulness.
