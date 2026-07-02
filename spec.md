# Spec — Log Insight (v1 / MVP)

Status: **APPROVED** — shaping signed off 2026-07-02 (BP2 tactical spec, BP3 §5 journald module,
BP8 §5.4 cursor semantics; see `breakpoint-register.md`). Implementation in progress (M0, M1 done).
Source of truth for intent: `intent.txt`. This spec refines it into something reviewable.

Shaping decisions (chosen by architect, 2026-07-02):
- **Stack:** Python 3 + FastAPI + SQLite (FTS5), served by uvicorn, Jinja2 templates.
- **Log sources:** *both* systemd journald *and* selected `/var/log` text files.
- **Watched file (v1):** `/var/log/syslog` only (single file for MVP).
- **Ingestion:** standalone collector run by **cron** (every 5 min), cursor-tracked, incremental.
- **Error overview:** time-bucketed histogram of error/warning counts (no baseline/threshold).
- **Build order:** risk-first accepted — journald-only is the graceful cut line (BP2).
- **Per-module mental model:** each collector gets a signed-off module spec *before* its code
  is written. `collectors/journald.py` is specified in §5 below; `collectors/files.py` is
  specified — and signed off — when we enter the files stage (**BP4**, see §6).

---

## 1. Functional (what)

**F1 — Search.** A localhost web UI where a user enters a keyword and gets matching log
lines. Full-text keyword match over the message text.

**F2 — Filter.** Search is combinable with:
- **time range** (from / to);
- **level** (e.g. error / warning / info / debug);
- **source** (journald unit, or file path).
Results show: timestamp, level, source, message. Newest first, paginated.

**F3 — Overview.** For the selected time range, a histogram of error+warning counts per
time bucket (bucket size derived from range, e.g. minute/hour/day). A spike is visible at a
glance. No "is this abnormal" logic — the shape carries the signal.

**F4 — Fresh data without manual action.** New log entries reach the DB automatically
(cron collector, every 5 min) and the UI surfaces them (auto-refresh of the current
query on an interval).

**Success criteria (from intent) → coverage:**
- "search by time range + keyword" → F1 + F2.
- "new entries appear without my action" → F4.
- "see at a glance if there were unusually many errors" → F3.

**Explicitly out of scope (v1):** alerting/notifications, multiple servers at once,
long-term archiving, authentication, log retention/rotation policy.

---

## 2. Architectural (how it fits)

Three decoupled parts over one SQLite database:

```
 cron (*/5) ─► collect.py ──writes──►  logs.db (SQLite, WAL, FTS5)  ◄──reads── app.py (FastAPI)
                  │                         ▲                                     │
      journald ◄──┤ collectors/journald.py  │  entries + entries_fts + state     └─► localhost UI
      /var/log ◄──┘ collectors/files.py     │                                        (127.0.0.1)
```

- **Single writer** (the cron collector), **read-only web app.** SQLite in **WAL mode** so
  the UI reads while the collector writes. This sidesteps write-contention entirely.
- **Localhost only:** uvicorn binds `127.0.0.1`. Not reachable off-host.
- **Multi-tenant seam (present, not built):** every entry carries a `host` column and no
  query assumes a single host. A future tenant/host dimension and auth layer can be added
  without reshaping the schema. We do **not** build isolation, tenants, or auth now.

**Normalized entry (shared shape across both sources):**

| field         | notes                                                            |
|---------------|-----------------------------------------------------------------|
| `id`          | autoincrement                                                   |
| `ts_utc`      | event time, UTC epoch (µs). journald: `__REALTIME_TIMESTAMP`; file: parsed + tz→UTC |
| `level`       | normalized enum: error / warning / info / debug / unknown       |
| `source_type` | `journald` \| `file`                                            |
| `source`      | unit name (journald) or absolute file path (file)               |
| `host`        | configured hostname (multi-tenant seam)                         |
| `message`     | the log text                                                    |

- **FTS5** virtual table over `message` (contentless / external-content) drives F1.
- **`state` table** holds ingestion cursors so collection is incremental and idempotent:
  - journald: the opaque **journald cursor** token (`--after-cursor`).
  - files: `(path, inode, byte_offset)` per watched file — handles rotation (inode change
    or shrink ⇒ re-read from start).

**Config** (`config.toml`): db path, host name, list of watched files, error-level mapping,
histogram bucket thresholds. Cron interval lives in the crontab (documented, not in app).

---

## 3. Governance & Security

- **G1 — Localhost binding is the primary control, and it is *enforced*.** No authentication
  in v1 *by design*, justified only because the service is bound to `127.0.0.1` and used by a
  small team with shell access to the host. The bind address is `server.host` in `config.toml`
  (default `127.0.0.1`) — configurable to preserve the multi-tenant seam — **but the app
  refuses to start on a non-loopback address unless `server.allow_nonloopback = true` is
  explicitly set**, and logs a loud warning when it is. Localhost-only is thus an enforced
  control, not a flippable default. **A future company-wide version MUST add authn/authz and
  network controls before any non-localhost binding.** This is a hard precondition, not an afterthought.
- **G2 — PII in scope.** Logs contain usernames, IPs, and operational data. Data stays at
  rest in `logs.db` on the same host; nothing is transmitted off-host. The SQLite file must
  be permission-restricted to the service user (it aggregates PII into one searchable place).
- **G3 — Collector privileges (dependency).** Reading journald and files like
  `/var/log/auth.log` typically needs elevated rights (group `systemd-journal` and/or `adm`,
  or root). The collector needs **read-only** access to logs and **write** access only to
  `logs.db`. Exact privilege model is a deploy decision — flagged in the plan.
- **G4 — No new egress.** The tool introduces no outbound network calls.

---

## 4. Tactical approach (what the human reviews the code against)

**Module map:**

| module                     | responsibility                                                        |
|----------------------------|-----------------------------------------------------------------------|
| `config.py` / `config.toml`| load settings (db path, host, watched files, level map, buckets)      |
| `db.py`                    | connect (WAL), create schema (`entries`, `entries_fts`, `state`)      |
| `normalize.py`             | the normalized-record shape + level mapping helpers                   |
| `collectors/journald.py`   | pull new entries via `journalctl -o json --after-cursor`; return records + new cursor |
| `collectors/files.py`      | read new bytes per watched file via inode+offset; parse syslog lines; return records + new offsets |
| `collect.py`               | **cron entrypoint** — run both collectors, insert in one transaction, update `state`; idempotent |
| `app.py`                   | FastAPI: search page, search API (keyword+time+level+source, paginated), histogram API; binds 127.0.0.1 |
| `templates/` + minimal JS  | search form, results table, histogram, interval auto-refresh          |
| `README.md`                | install, cron setup, required privileges (G3), how to run             |

**Build order — risk-first, to protect the ~1-day budget:**

1. `db.py` schema + `normalize.py` shape.
2. **`collectors/journald.py`** — structured JSON, lowest risk.
3. **`app.py` + templates** — search, filters, histogram, auto-refresh, over journald data.
   *At this point every success criterion is already met.*
4. **`collectors/files.py`** — the fragile part (regex parsing, missing year, tz guessing,
   rotation). Built last.

**⚠ Risk & recommended cut line (from choosing "Both").** `/var/log` text parsing is the
single riskiest task and the likeliest to overrun a day. Because it is built **last** and is
a separate collector, journald-only ingestion is a clean **graceful cut line**: if the budget
runs out at step 4, the tool still fully meets all three success criteria on journald data,
and files can land in a follow-up. I recommend accepting this ordering explicitly.

**Key technical stances (review against these):**
- Time is stored as **UTC epoch**; displayed in server-local tz. File timestamps missing a
  year assume current year (with Dec→Jan rollover guard); missing tz assumes server-local.
- Search = FTS5 `MATCH` on message, `AND`-ed with `ts_utc BETWEEN`, `level IN`, `source =`
  filters; ordered `ts_utc DESC`; page via limit/offset.
- File level detection is a **heuristic** (keyword/format based) → may yield `unknown`;
  journald level comes reliably from `PRIORITY`. The overview counts `error`+`warning`.
- Collector is **idempotent via cursors**: re-running mid-window never double-inserts.

---

## Open items — resolved at sign-off (2026-07-02)
1. Risk-first build order with journald-only cut line — **accepted.**
2. Watched files v1 — **`/var/log/syslog` only.**
3. Collector privilege model — **deferred to the plan phase** (G3).

---

## 5. Module spec — `collectors/journald.py`  (mental model for code review)

**Review this section against the code.** The module is a **pure transform**: given the last
cursor, it reads new journal entries and returns normalized records + the new cursor. It does
**not** touch the DB — `collect.py` persists what this returns. That separation is what makes
it reviewable and unit-testable.

### 5.1 Contract

```python
def collect_journald(
    after_cursor: str | None,      # persisted journald cursor; None on first run
    host: str,                     # configured host name (multi-tenant seam)
    *,
    initial_backfill: str,         # e.g. "24h" | "boot" | "all" — only used when after_cursor is None
    max_batch: int,                # cap entries read per run (bounds the DB transaction)
    reader=_journalctl_reader,     # injectable: yields raw JSON lines (default shells out)
) -> JournaldBatch                 # -> {records: list[LogRecord], new_cursor: str | None, skipped: int}
```

- `records` — normalized `LogRecord`s (shape from `normalize.py`).
- `new_cursor` — cursor to persist; semantics defined in §5.4 (the last *successfully
  JSON-parsed* entry's `__CURSOR`, kept or content-dropped). If nothing new was parsed,
  returns `after_cursor` unchanged (caller writes nothing).
- `skipped` — count of unparseable lines (for logging/observability).

### 5.2 Data flow

1. Build the `journalctl` argv:
   - have cursor → `journalctl -o json --no-pager --after-cursor <cursor>` (`--after-cursor`
     is **exclusive** → the cursor entry is never re-emitted → no duplicates).
   - no cursor → seed from `initial_backfill`: `--since -24h` / `--boot` / (nothing = all).
     Bounded backfill avoids ingesting the entire journal on first run.
2. Stream stdout **line by line**; parse each as one JSON object.
3. **Cap at `max_batch`**: read up to N entries, then stop and close the process. Remaining
   entries are picked up by the next cron tick (cursor advances). Keeps memory and each
   transaction bounded even on a large first run.
4. Map each entry → `LogRecord` (§5.3); track the last `__CURSOR` seen as `new_cursor`.

### 5.3 Field mapping (journald JSON → LogRecord)

| journald field           | LogRecord    | rule                                                                 |
|--------------------------|--------------|----------------------------------------------------------------------|
| `__REALTIME_TIMESTAMP`   | `ts_utc`     | `int(...)` — already µs since epoch, UTC. Missing ⇒ skip the entry.  |
| `PRIORITY`               | `level`      | 0–3 → error, 4 → warning, 5–6 → info, 7 → debug, missing/other → unknown |
| `MESSAGE`                | `message`    | str as-is; **if list[int]** (binary) → `bytes(...).decode("utf-8","replace")`; missing → `""` |
| `_SYSTEMD_UNIT` › `SYSLOG_IDENTIFIER` › `_COMM` | `source` | first present; else `"unknown"`         |
| — (config)               | `host`       | the passed-in `host`, **not** journald `_HOSTNAME` (seam decision)   |
| — (constant)             | `source_type`| `"journald"`                                                        |

### 5.4 Cursor advancement, idempotency & error handling

- **Cursor advancement (BP8, option B).** `new_cursor` = the `__CURSOR` of the **last
  successfully JSON-parsed entry** this run — *whether it was kept or content-dropped* (e.g.
  dropped for a missing timestamp). Rationale: a parseable-but-unusable entry is progressed
  past **once**, not re-read and re-skipped on every cron run.
  - A **trailing unparseable line** (malformed / truncated read) has no cursor and does **not**
    advance `new_cursor` beyond the last good parse before it → it is safely retried next run.
  - A **malformed middle line** is skipped; a later good entry's cursor supersedes it (that
    middle entry is not recoverable — it had no readable cursor either way).
- **Idempotent via cursor.** Re-running mid-window never double-inserts (exclusive
  `--after-cursor` + same insert path). A crash before `collect.py` persists the cursor just
  re-reads those entries next run — harmless when the insert is dedup-safe; see `collect.py` note.
- **Stale/invalid cursor** (journal vacuumed away): `journalctl` errors or emits nothing.
  → log a warning and fall back to `initial_backfill` for this run, then continue.
- **Malformed JSON line** → skip it, increment `skipped`, keep going (never abort the batch).
- **`journalctl` missing / non-zero exit** → raise `CollectorError`. `collect.py` catches
  **per-collector**, so a broken journald read does not stop the file collector, and vice versa.

### 5.5 Cross-module contracts this implies
- `normalize.py` owns the `LogRecord` shape and the **PRIORITY→level** map (shared, so the
  file collector maps to the same `level` enum).
- `collect.py` persists records **and** `new_cursor` in **one transaction**, and should make
  inserts dedup-safe (e.g. carry `__CURSOR` as a unique key, or rely on the exclusive-cursor
  guarantee) so a cursor-not-yet-saved crash cannot create duplicates.
- `config.toml` gains: `journald.initial_backfill`, `journald.max_batch`.

### 5.6 Testability (how review confirms behavior)
Because `reader` is injectable, unit tests feed canned journald JSON lines and assert the
returned `records`, `new_cursor`, and `skipped` — no real journal needed. Cases to cover:
empty batch, `max_batch` truncation, binary `MESSAGE`, missing `PRIORITY`, missing timestamp,
malformed line, unit-fallback chain.

---

## 7. Web UI — PROVISIONAL design (M2 / BP5 — pending real UX sign-off)

Built to preliminary layout preferences; **BP5 is reopened** — this needs a browser UX review by
business before it is signed off, and change requests get folded back into this section first.
Server-rendered (FastAPI + Jinja2), read-only over the DB.

- **Single page**, top→bottom: filter bar → error+warning histogram → results table (BP5).
- **Filters (F1/F2):** keyword (FTS), From/To (datetime-local, server-local tz → UTC µs),
  level (all/one), source (exact). Combined as `AND`.
- **Histogram (F3):** error+warning counts per bucket; bucket size auto-derived from span
  (`overview.*` config), **capped at ~1500 buckets** so an extreme range can't render tens of
  thousands of bars. Respects keyword/source/time filters; ignores the level filter by design.
- **Freshness (F4):** auto-refresh every **30s** with a pause toggle (persisted); reloading the
  page re-runs the current query so new entries appear (BP5).
- **Paging:** **keyset "Load older"** (`/api/search?before_ts&before_id`) appends older rows via
  minimal JS without disturbing the live view (BP5). `id` breaks ts ties.
- **Security:** localhost bind enforced by `enforce_bind_policy` at serve time (G1). Output is
  Jinja-autoescaped; JS appends use `textContent` (no HTML injection from log messages).
- **Deviation (approved by self-review):** the histogram is **server-rendered on `GET /`**, not a
  separate `/api/histogram` endpoint — auto-refresh reloads the page and recomputes it, so a
  second endpoint would be dead weight. `queries.histogram()` is still independently unit-tested.

## 6. Deferred module spec — `collectors/files.py`  (BP4)

`collectors/files.py` is **not** specified yet. Text-file parsing (regex, missing year, tz
guessing, rotation via inode+offset) carries the real design risk, and per the agreed
per-module discipline it gets its **own §-level mental-model spec written and signed off
before any file-collector code** — mirroring §5. Entering the files stage triggers **BP4**;
we shape and sign off `collectors/files.py` there. Until then, journald is the working system.
