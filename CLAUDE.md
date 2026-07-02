# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

Greenfield. As of this writing the repo contains only `intent.txt` (the project brief) and the `breakpoint` skill — no application code, build system, or tests yet. Do not invent build/lint/test commands; add them here once the stack is chosen and set up.

## Mandatory workflow: Breakpoint.

**Any** software task in this repo — implementing, changing, extending, or planning — runs under the `breakpoint` skill (`.skills/breakpoint/SKILL.md`). This is not optional. Read that file before doing work. The essentials:

- **Never guess silently.** When something is undecided, missing, unsafe, or an assumption doesn't hold, STOP and call a visible `|| BREAKPOINT` (format is in the skill). You *propose*; the human *decides*. Never resolve a breakpoint yourself — not even by building a mock or picking the "obvious" option.
- **Flow:** Intent → Shaping (spec with functional / architectural / governance & security / **tactical** parts) → Plan → Plan Calibration `(||)` → Generation → Verification → Validation. Announce each phase; stop at every `(||)`.
- The **tactical spec is what the human reviews the code against** — it must let the architect build a mental model without reading every line. Present options with trade-offs; get sign-off **before writing any code**.
- **Scale the weight to the task.** Phases are always present but a one-line ticket gets a one-paragraph shaping; a large feature gets the full treatment and may run per-module.
- **Re-shaping:** when new insight changes what was agreed, update **both** the affected `spec*.md` and `plan.md` together, get explicit confirmation, and record it (commit + `## Revisionshistorie` changelog entry). Nothing changes silently.
- Maintain `breakpoint-register.md`: one line per breakpoint (id, type, opened, resolution, closed).
- **Communicate with the user in the language they use** — the intent is in German, so default to German.

Artifacts this workflow expects to exist as work proceeds: `spec*.md`, `plan.md`, `breakpoint-register.md`.

## Product intent (see `intent.txt`)

"Log Insight" — an internal tool to collect a single Linux server's logs into a database regularly and provide a simple web UI to search, filter, and get a rough overview (e.g. events over time). Replaces manual `grep`-over-SSH during incidents.

**Success criteria:** search by time range + keyword and get matching log lines; new entries appear in the view automatically without manual action; an at-a-glance signal for unusually many errors.

**Out of scope (v1):** alerting/notifications; multiple servers at once; long-term archiving.

### Hard constraints — do not violate without a breakpoint
- **Single server, internal, small team.** Not multi-tenant, no cloud requirement — but architect so a *future* multi-tenant evolution is not blocked. Do not build or specify multi-tenancy now.
- **Localhost-only access in v1.** Logs contain usernames, IP addresses, and other operational data, so the first version must only be reachable via localhost. A company-wide-usable structure should not be architecturally prevented, but must not be developed or specified at this stage.
- **~1 day of effort — keep it small, MVP.** Deliberately minimal.
