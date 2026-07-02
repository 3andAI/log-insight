# Breakpoint Register

One line per breakpoint. Basis for the team's Flow-Uptime metric. Keep it honest.

| id  | type     | opened     | situation                                             | resolution                                                                                     | closed     |
|-----|----------|------------|------------------------------------------------------|------------------------------------------------------------------------------------------------|------------|
| BP1 | DECISION | 2026-07-02 | Intent silent on stack, log source, ingestion, error-view design | Architect chose: Python+FastAPI+SQLite(FTS5); both journald & /var/log; cron; time-bucketed histogram | 2026-07-02 |
| BP2 | DECISION | 2026-07-02 | Tactical spec sign-off + open items (build order, watched files, collector privileges) | Build order accepted (journald-only = cut line); watched file = /var/log/syslog only; privilege model deferred to plan | 2026-07-02 |
| BP3 | DECISION | 2026-07-02 | Module-level mental-model spec for collectors/journald.py must be signed off before its code | Architect signed off spec §5 as written (contract, max_batch bounded batch, stale-cursor→backfill) | 2026-07-02 |
| BP4 | TECHNICAL| 2026-07-02 | Deferred: collectors/files.py needs its own module-level spec (§6) written & signed off before file-collector code | — planned; opens at M4 (files stage) —                                                          | open       |
| BP5 | DECISION | 2026-07-02 | Planned: UI mock / UX sign-off before building the web app (M2 start)                          | — planned; opens at M2 start —                                                                  | open       |
| BP6 | DEPENDENCY| 2026-07-02| Planned: collector privilege model (G3) before production ingestion (M3)                       | — planned; opens pre-deploy at M3 —                                                             | open       |
| BP7 | DECISION | 2026-07-02 | Plan Calibration — architect must approve plan.md before any code                             | Architect approved plan (incl. G1 bind-guard + M0.5 journalctl probe amendments)               | 2026-07-02 |
