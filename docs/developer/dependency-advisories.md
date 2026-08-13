# Dependency Advisories

Last updated: 2026-08-11

How Atlas handles dependency vulnerability reports, and what blocks a merge.

## What blocks, and what only reports

The `Security Checks` workflow runs several scanners. Most of them report
without failing, deliberately: they scan the entire development environment,
where the majority of findings are in build and test tooling that never reaches
a user. A job that fails on those trains people to ignore it, which is worse
than not running it.

Two checks **do** block:

| Check | What it enforces |
|---|---|
| Lockfile is in sync | `uv export --locked` succeeds, so `uv.lock` matches `pyproject.toml` |
| Production frontend advisories | No high or critical advisory in `npm audit --omit=dev` |

Everything else — Safety, Bandit, Semgrep, Trivy, Grype, the full `npm audit` —
is advisory. Read the summary comment on the PR; do not treat a green
`Security Checks` as "no known vulnerabilities anywhere".

## Why the lockfile check exists

A stale lock does not announce itself. Before this check was added, `uv.lock`
pinned `reportlab` at 4.4.10 while `pyproject.toml` declared
`reportlab>=5.0.0,<6.0.0` — the lock resolved *below its own declared floor*,
and `uv export --locked` had been failing for some time with nobody watching.

`uv export --locked` fails rather than regenerating, which is what makes it a
check rather than a build step. If it fails:

```bash
uv lock          # regenerate
git diff uv.lock # review every version change before committing
```

Review the diff. A lock refresh can move dozens of transitive packages, and
"the tool did it" is not a review.

## Why the frontend gate is production-only

`npm audit` on the full tree is dominated by `vitest`, `vite`, `esbuild`,
`picomatch`, and friends. None are in the browser bundle; none are reachable
from user input at runtime. The `esbuild` advisory, for instance, concerns its
dev server, which does not exist in a deployed Atlas.

`--omit=dev` narrows this to what is actually served. That set should stay at
zero high/critical, and it is reasonable to block on.

## Accepting an advisory

If a high or critical advisory lands in a production dependency and is not
exploitable here, do not weaken the gate. Record it, with the reasoning, in the
table below, and set an expiry so it gets revisited rather than becoming
permanent.

| Advisory | Package | Accepted on | Expires | Reasoning |
|---|---|---|---|---|
| _(none currently)_ | | | | |

An entry needs to say *why it is not reachable in this application* —
specifically, not "low severity in practice". If that case cannot be made,
upgrade or replace the dependency.

## Current known state

As of 2026-08-11, after `npm audit fix`:

- **Production frontend dependencies**: 0 advisories.
- **Full frontend tree**: 5 (1 critical, 1 high, 3 moderate), all in `vitest`,
  `@vitest/mocker`, `vite`, `vite-node`, and `esbuild`. Clearing them needs a
  `vitest` major bump, which belongs with a deliberate tooling upgrade rather
  than a security push. None ship to a browser.
- **Python**: `uv.lock` is in sync with `pyproject.toml`. Safety findings are
  reported in the PR summary and are not currently gated — the resolved
  production set is a smaller surface than the dev environment Safety scans,
  and gating on the latter would block on test tooling.
