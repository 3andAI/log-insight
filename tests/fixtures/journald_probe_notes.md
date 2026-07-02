# M0.5 — journalctl probe notes

Record of the plan's M0.5 de-risking step. Purpose: verify spec §5.3 (journald → LogRecord
field mapping) against **real** `journalctl -o json` output before writing `collectors/journald.py`.

## Environment probed
- Dev host (WSL2), systemd 245, journald available and running.
- Sample: `journalctl -o json --no-pager -n 800` (800 entries, **0 malformed**).
- ⚠ Not the production target. Per plan M0.5, obtain a sanitized target-server sample by M3
  and re-check; open a breakpoint if it reveals mapping gaps.

## Findings vs spec §5.3 — CONFIRMED, no spec change
| assumption (§5.3)                         | observed                                                              |
|-------------------------------------------|-----------------------------------------------------------------------|
| `__REALTIME_TIMESTAMP` = all-digit µs UTC | 800/800 present, all digit-strings ✓                                   |
| `__CURSOR` present (cursor tracking)      | 800/800 present ✓                                                     |
| `MESSAGE` may be str or `list[int]`       | all `str` in sample; binary path unobserved → kept defensive          |
| `PRIORITY` 0–7, may be missing            | observed 3,4,5,6,7; always present; 0–2 & missing unobserved → defensive |
| source fallback `_SYSTEMD_UNIT › SYSLOG_IDENTIFIER › _COMM › unknown` | `_SYSTEMD_UNIT`×629; **137** have SYSLOG_IDENTIFIER but no unit (fallback exercised); **34** have no source at all → `unknown` |
| malformed lines possible                  | `journalctl -o json` emitted none; skip+count path is defensive       |

Reality matches §5.3. Defensive paths (binary MESSAGE, missing PRIORITY, missing timestamp,
malformed line) are unobserved in a clean journal but real enough (kernel binary msgs, truncated
reads) to keep — the fixture synthesizes each so M1 tests exercise them.

## Golden fixture — `journald_sample.jsonl`
**Sanitized / synthetic content, real structure.** No real PII (usernames/IPs/host/machine-id
scrubbed). Deterministic timestamps (fixed anchor, not wall-clock). 12 JSON entries + 1 malformed
line. Expected `collect_journald` mapping (drives M1 tests, spec §5.6):

| # | exercises                          | → level  | → source (rule)                     | notes                    |
|---|------------------------------------|----------|-------------------------------------|--------------------------|
| 1 | PRIORITY 6, unit+ident             | info     | `session-42.scope` (_SYSTEMD_UNIT)  | normal case              |
| 2 | PRIORITY 3                         | error    | `app.service`                       | 0–3 → error              |
| 3 | PRIORITY 4                         | warning  | `monitor.service`                   |                          |
| 4 | PRIORITY 5                         | info     | `app.service`                       | 5–6 → info               |
| 5 | PRIORITY 7                         | debug    | `app.service`                       |                          |
| 6 | PRIORITY 0                         | error    | `kernel` (SYSLOG_IDENTIFIER)        | 0 boundary               |
| 7 | no _SYSTEMD_UNIT, has identifier   | info     | `CRON` (SYSLOG_IDENTIFIER fallback) | fallback step 2          |
| 8 | only _COMM                         | info     | `sshd` (_COMM fallback)             | fallback step 3          |
| 9 | no source field                    | info     | `unknown`                           | fallback terminal        |
|10 | MESSAGE = list[int]                | info     | `weird.service`                     | decode → `binary hejä`   |
|11 | missing PRIORITY                   | unknown  | `app.service`                       | missing → unknown        |
|12 | missing __REALTIME_TIMESTAMP       | —        | —                                   | **entry SKIPPED**        |
|13 | malformed JSON line                | —        | —                                   | **skipped + counted**    |

Expected from `collect_journald(after_cursor=None, host="target-host", reader=<file lines>)`:
- `records`: 11 (entries 1–11; #12 content-dropped for missing ts).
- `new_cursor`: **entry 12's** `__CURSOR` = `s=1a2b3c;i=c;b=0f0f;m=abc;t=00;x=deadbeef12`
  (BP8 option B — last *successfully parsed* entry, kept or dropped; the trailing malformed
  line 13 does **not** advance the cursor).
- `skipped`: 2 (entry 12 missing ts + line 13 malformed).
- every record's `host` == `"target-host"` (from param, not `_HOSTNAME`), `source_type` == `"journald"`.
