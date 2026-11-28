# 0045. 1,000-commit C/C++ audit window

- Status: accepted
- Date: 2026-11-27

## Context

The C/C++ corpus initially covered 100 first-parent commits per repository. That
is enough to catch crashes and obvious extraction gaps, but not enough to assess
long-window runtime, cache behavior, or history noise. Other real-world audits
use 1,000-commit windows.

## Decision

Add a 1,000-commit window as the diagnostic deep pass for the C/C++ corpus,
using the existing pinned manifest and runner:

```sh
python benchmarks/cfamily/run.py \
  --offline --limit 1000 --jobs 8 \
  --output /tmp/cfamily-audit-1k.json
```

Record per-repository runtime, findings, commits with findings, rules, and
errors in `docs/c-cpp-audit.md`. Keep the 100-commit window as the quick smoke.

The 1,000-commit pass remains diagnostic and network-independent after the
initial cache population. It is not a CI gate.

## Consequences

Long-window runtime and incremental history behavior become measurable. Finding
counts may include large historical migrations and intentional API changes, so
they are not defect labels. A failed or pathological repository can be identified
without changing the default 100-commit corpus.

## Alternatives considered

- Run 1,000 commits in CI. Rejected: external clones and runtime.
- Replace the 100-commit smoke. Rejected: quick local iteration remains useful.
- Use 10,000 commits. Rejected: diminishing value and much higher runtime.

## Rollback

Remove the 1,000-commit section/report results and this ADR; keep the pinned
100-commit corpus.
