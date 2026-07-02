# Plan — Log Insight (v1 / MVP)

Derived from `spec.md`. Status: **DRAFT — awaiting Plan Calibration sign-off (Breakpoint phase 4).**
No code is written until this plan is approved.

Order is **risk-first** (spec §4): journald path delivers every success criterion; `/var/log`
is last and is the graceful cut line.

---

## Milestones

### M0 — Scaffolding & foundations
- Repo layout, `pyproject.toml`/deps (fastapi, uvicorn, jinja2; stdlib sqlite3, tomllib).
- `config.py` + `config.toml`: db path, `host`, `server.host`/`server.port`/`server.allow_nonloopback`
  (G1 guard), watched files (`["/var/log/syslog"]`), `journald.initial_backfill`,
  `journald.max_batch`, histogram bucket thresholds, level map.
- `db.py`: connect in **WAL**, create `entries`, `entries_fts` (FTS5 external-content over
  `message`), `state` (cursors: journald token + per-file inode/offset).
- `normalize.py`: `LogRecord` shape + shared **PRIORITY→level** map.
- **Tests:** schema creates cleanly & is idempotent; WAL active; PRIORITY→level mapping table;
  FTS insert/match round-trip on a sample row.

### M0.5 — journalctl probe (de-risk before building the collector)
- Capture real `journalctl -o json` output here (dev has systemd 245 + journald confirmed
  available) and **verify spec §5.3 field mapping against reality** (PRIORITY, `__REALTIME_TIMESTAMP`,
  `MESSAGE` shapes, unit fields). Reconcile any surprise into §5.3 before writing code.
- **Sanitize PII**, commit sanitized lines as `tests/fixtures/journald_sample.jsonl` — the golden
  fixture M1's injectable `reader` tests run against.
- **Dependency note:** the WSL2 dev journal ≠ the target server (different units/formats/volume).
  Obtain a representative **sanitized sample from the target server by M3** to cover real edge
  cases; flag as a breakpoint if that sample reveals mapping gaps.

### M1 — Ingestion (journald)
- `collectors/journald.py` **exactly to spec §5** (contract, bounded `max_batch`, exclusive
  cursor, field mapping, stale-cursor→backfill, per-collector `CollectorError`).
- `collect.py` (cron entrypoint): run journald collector, persist records **+ new_cursor in one
  transaction**, dedup-safe insert; catch errors **per-collector**; concise run log (counts, skipped).
- **Tests (per spec §5.6, injected `reader`):** empty batch; `max_batch` truncation + cursor
  advance; binary `MESSAGE`; missing PRIORITY→unknown; missing timestamp→skip; malformed line→skip+count;
  unit fallback chain. Plus `collect.py`: **idempotent re-run inserts no duplicates**; transaction
  rolls back atomically on failure.

### M2 — Web app (search + overview)
- **(||) Foreseen breakpoint BP5 — UI mock sign-off (business/UX), placed at M2 start.** A quick
  static sketch (search form, results table, histogram, auto-refresh) signed off before building
  the full app, so layout/UX changes are cheap.
- `app.py` (FastAPI, bind from `server.host`, default `127.0.0.1`, **with the non-loopback
  startup guard, G1**): search page + JSON API (keyword FTS `MATCH` AND-ed with `ts_utc BETWEEN`,
  `level IN`, `source =`; `ts_utc DESC`; paginated); histogram API (bucketed error+warning counts).
  `templates/` + minimal JS for interval auto-refresh.
- **Tests:** filter combinations (keyword/time/level/source); FTS match correctness; histogram
  bucketing across ranges; pagination; **guard rejects a non-loopback host unless `allow_nonloopback`,
  default bind is loopback**.
- *At end of M2 all three success criteria are met on journald data.*

### M3 — Deployment (journald path live)
- **(||) Foreseen breakpoint BP6 — collector privilege model (G3).** Resolve before production
  ingestion: dedicated service user in `systemd-journal`/`adm` groups (read-only logs, write only
  `logs.db`) vs root. Recommendation drafted at that point.
- `README.md`: install, `config.toml`, **cron** entry (`*/5`), required privileges, restricted
  `logs.db` file perms (G2).
- **Manual verification on target:** cron tick ingests new entries; UI shows them after
  auto-refresh; histogram reflects an induced error burst.

### M4 — `/var/log/syslog` ingestion (the cut line)
- **(||) Breakpoint BP4 — shape `collectors/files.py` module spec (spec §6) and sign off before
  any file-collector code.** Mirrors §5: syslog regex, missing-year (Dec→Jan guard), tz→UTC,
  rotation via inode+offset.
- Then build `collectors/files.py`, wire into `collect.py`, and test: parse sample syslog lines;
  level heuristic; incremental read via offset; **rotation** (inode change / shrink → re-read).
- **Budget cut line:** if the day runs out before M4, journald-only ships and files land in a
  follow-up — no success criterion is lost.

---

## Verification & Validation (applied per completed milestone, spec workflow §6–7)
- **6a Self-review against the spec** (not the prompt) — list & resolve every deviation.
- **6b Independent review** — user runs a review with a *different* model; work every finding.
- **6c Architect sign-off (||)** against tactical + architectural spec.
- **Validation (||):** check the build against governance/security spec (G1 localhost-only,
  G2 PII-at-rest/file perms, G4 no egress) and functional criteria; report before asking for
  Skipped / Passed / Gaps.

## Foreseen breakpoints (ordered)
| id  | when            | type      | decision                                             |
|-----|-----------------|-----------|------------------------------------------------------|
| BP5 | M2 start        | DECISION  | UI mock / UX sign-off before building the app        |
| BP6 | M3 (pre-deploy) | DEPENDENCY| collector privilege model (G3)                       |
| BP4 | M4 start        | TECHNICAL | `collectors/files.py` module spec + sign-off (§6)    |

## Revisionshistorie
- 2026-07-02 — Initial plan derived from `spec.md`. Awaiting Plan Calibration (BP7).
- 2026-07-02 — Calibration amendments (architect Q&A): added **M0.5 journalctl probe** (verify
  §5.3 + golden fixture); made server bind configurable (`server.host`) with an **enforced
  non-loopback startup guard** (spec §3 G1). Still awaiting Plan Calibration (BP7).
