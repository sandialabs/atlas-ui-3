# Atlas UI 3 Documentation

Last updated: 2026-06-22

This is the documentation hub for Atlas UI 3. Each area below has its own
`README.md` index — start there for the full list of pages in that area.

## Map

| Area | Audience | What's inside |
| --- | --- | --- |
| [getting-started/](getting-started/README.md) | Everyone | Install, run locally, use the Python package |
| [user-guide/](user-guide/README.md) | End users | How to use specific in-app features |
| [admin/](admin/README.md) | Operators | Configuration, security, storage, operations |
| [developer/](developer/README.md) | Contributors | Architecture, conventions, MCP development, release process |
| [developer/design-notes/](developer/design-notes/README.md) | Contributors | Point-in-time records of how shipped features were built |
| [agentportal/](agentportal/README.md) | Operators / Contributors | Agent Portal (dev-preview): threat model, design, CLI |
| [telemetry/](telemetry/README.md) | Operators / Contributors | OpenTelemetry audit trail and analysis |
| [planning/](planning/README.md) | Contributors | Active design proposals and roadmap notes |
| [testing/](testing/README.md) | Contributors | Manual test checklists and procedures |
| [example/](example/README.md) | Operators | Deployment and configuration examples |
| [archive/](archive/README.md) | Reference only | Completed/superseded plans, kept for history |

Image assets live in [`readme_img/`](readme_img/) (used by the project README)
and [`developer/images/`](developer/images/) (used by developer docs and
design notes).

## Where does a new doc go?

| The doc is… | Put it in |
| --- | --- |
| An operator/configuration/deployment guide | `admin/` |
| Durable contributor reference (architecture, conventions, how a subsystem works) | `developer/` |
| A how-to for an end-user-facing feature | `user-guide/` |
| A record of *how/why* a specific feature was built on a date | `developer/design-notes/` |
| A proposal or plan for work **not yet done** | `planning/` |
| A plan/proposal that has **shipped or been abandoned** | `archive/` |

Conventions:

- **One index per directory.** Add new pages to the directory's `README.md`
  so they stay discoverable. `scripts/check-docs.sh` (run in CI) fails if a
  doc is orphaned or a relative link is broken.
- **Filenames are `kebab-case`.** Date-stamp only point-in-time records
  (design notes, archived plans) as `topic-YYYY-MM-DD.md`; evergreen reference
  docs carry a `Last updated:` line instead.
- **`archive/` is excluded from the documentation bundle** shipped to AI
  agents (see [developer/documentation-bundling.md](developer/documentation-bundling.md)).
  Move a doc there when it stops describing how the system currently works.
