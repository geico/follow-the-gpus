---
title: "Results and logs as 2 streams: the stdout/stderr analogy, and a plan for shipping logs to the store"
date: 2026-10-14
---

# Results and logs as 2 streams: the stdout/stderr analogy, and a plan for shipping logs to the store

## Summary

A probe result (1 row) and a log event (1 line of info, warning, error, or debug text) are 2
different signals, in the sense that OpenTelemetry defines a metric and a log as 2 different
signals, not 2 severities of the same thing
([OpenTelemetry concepts, signals](https://opentelemetry.io/docs/concepts/signals/)). The
stdout/stderr analogy is right at that level: a row is the program's product, a
log line at any severity is a diagnostic about the program, the same split that C and POSIX
draw between the normal output stream and the error stream
([man7.org `stdout(3)`](https://man7.org/linux/man-pages/man3/stdout.3.html)). The analogy
breaks on 1 point: a provider error does not split neatly into "the row" and "the diagnostic."
A probe that returns a handled outcome, even a failed one, still writes a row with a short
`error_code` ([`prober/ftg_prober/record.py`](../prober/ftg_prober/record.py) line 92); only
the unhandled case, an exception that escapes the probe, writes no row at all
([`prober/ftg_prober/scheduler.py`](../prober/ftg_prober/scheduler.py) lines 194-198). The log
carries the full provider error text in both cases.

Today, every log line of every severity goes to stdout only
([`prober/ftg_prober/log.py`](../prober/ftg_prober/log.py)), which is the Twelve-Factor App
model, not the 2-destination model that this note proposes
([12factor.net, Logs](https://12factor.net/logs)). This note recommends the 2 streams, their destinations, which levels ship, the record
fields, and what happens when the store is not yet open or is down.

## 1. Is the stdout/stderr analogy right?

Yes, for severity: every log line, at every level from debug to error, belongs on the
diagnostic side, never the result side. POSIX and C give `stdout` to "the normal output
stream" and `stderr` to "the error stream," for printing diagnostic or error messages, kept
separate so a consumer can read results without diagnostics in the way
([man7.org `stdout(3)`](https://man7.org/linux/man-pages/man3/stdout.3.html);
[GNU C Library manual, Standard Streams](https://www.gnu.org/software/libc/manual/html_node/Standard-Streams.html)).
Python's `logging` module keeps 1 severity ladder, DEBUG through CRITICAL, for exactly this
kind of diagnostic text; none of its levels describe a program's result
([Python docs, Logging Levels](https://docs.python.org/3/library/logging.html#logging-levels)).
OpenTelemetry's log record carries a `SeverityNumber` on the same ladder (DEBUG 5-8, INFO 9-12,
WARN 13-16, ERROR 17-20), confirming that severity is an axis of 1 signal, logs, not a
boundary between 2 signals
([OpenTelemetry, Logs Data Model](https://opentelemetry.io/docs/specs/otel/logs/data-model/)).

This project's rows are not log lines at any level; they are closer to what OpenTelemetry
calls a metric, "a measurement captured at runtime," and what Prometheus calls a time series,
a sample keyed by a name and a fixed set of labels at a point in time
([OpenTelemetry concepts, signals](https://opentelemetry.io/docs/concepts/signals/);
[Prometheus docs, data model](https://prometheus.io/docs/concepts/data_model/)). A row's
`sku`, `region`, and `probe_type` fields play the role of Prometheus labels; `outcome` and
`n_placed` play the role of the sampled value
([`prober/ftg_prober/record.py`](../prober/ftg_prober/record.py) lines 77-94). So "results are
stdout" is right in spirit (the program's product, meant for a reader of the data) and
"diagnostics at every level are stderr" is right exactly (none of them are results), but the
closer framing is results-as-metrics and diagnostics-as-logs, 2 separate OpenTelemetry
signals, not 2 priorities of 1 stream.

Where the analogy breaks: a provider error. A common example, "a probe that fails:
its row is a result, its error message is a diagnostic," holds only for a handled failure. A
probe that catches a provider error inside its own call and turns it into an outcome still
writes a row, with a short `error_code` field such as a throttling code
([`prober/ftg_prober/record.py`](../prober/ftg_prober/record.py) line 92;
`OUTCOMES` includes `"error"`, `"quota"`, `"timeout"`, line 53). Only an exception that escapes
the probe entirely, caught by the scheduler's own `try/except`, writes no row; it only logs
`probe.failed` with the error text
([`prober/ftg_prober/scheduler.py`](../prober/ftg_prober/scheduler.py) lines 194-198). So the
real split is not success against failure. It is a short, structured, bounded field that can
sit in a row (`error_code`) against the full, unbounded provider error text, which belongs
only in the log, never in a row, by the design rule of this project. That matches the
C split, too: `stdout` is line-structured for a reader to parse; `stderr` is free text for a
human.

## 2. Classifying the events this code already emits

Read from `prober/ftg_prober/log.py`, `scheduler.py`, `store.py`, `record.py`, `cli.py`,
`probes.py`, the `aws/` and `azure/` probe modules, and `sweeper/azure/sweeper/run.py` and
`function_app.py` (`grep -rn "log\.\|logger\."`).

| Event | Level | Module | What it is |
|---|---|---|---|
| `probe.loaded` | INFO | `probes.py` | startup: a probe module registered |
| `run.start`, `sim.start`, `sim.done` | INFO | `cli.py` | process lifecycle |
| `run.signal` | INFO | `cli.py` | the process caught a shutdown signal |
| `call.throttled` | INFO | `scheduler.py` | a queued call was throttled and re-queued with backoff |
| `probe.done` | INFO | `scheduler.py` | a probe finished; carries `rows`, `ms` |
| `probe.empty` | INFO | `scheduler.py` | a probe returned 0 rows |
| `store.put` | INFO | `scheduler.py` | 1 object written; carries `key`, `rows`, `bytes` |
| `scheduler.stopped` | INFO | `scheduler.py` | the run loop exited; carries `calls` |
| `row.invalid` | WARNING | `scheduler.py` | a row failed `Record.validate()` before the write |
| `az_spot.query_failed` | WARNING | `azure/spot_resource_graph.py` | an Azure Resource Graph query failed |
| `allocation.teardown_unverified` | WARNING | `azure/capacity_reservation.py`, `azure/vmss_flex.py` | cleanup could not confirm the resource was gone |
| `allocation.tag_failed` | WARNING | `azure/vmss_flex.py` | a tag write failed after allocation |
| `spot_score.skipped` (frozen) | WARNING | `aws/spot_score.py` | the Spot score request list is frozen |
| `odcr.teardown_unverified` | WARNING | `aws/odcr.py` | same pattern as the Azure teardown case, on AWS |
| `probe.failed` | ERROR (`log.exception`) | `scheduler.py` | an exception escaped a probe; no row is written |
| `allocation.failed` | ERROR (`log.exception`) | `azure/capacity_reservation.py` | an allocation attempt raised |
| `odcr.ambiguous_cleanup_failed`, `odcr.cleanup_failed` | ERROR (`log.exception`) | `aws/odcr.py` | cleanup of a reservation raised |
| `azure`/`azure.identity`/`azure.core` SDK lines | INFO (suppressed to WARNING unless `--log-level DEBUG`) | SDK, via `log.py` | 1 line per token request and per HTTP call |
| `sweeper timer: past due`, result health line, decision lines | INFO/ERROR | `sweeper/azure/sweeper/run.py`, `function_app.py` | the sweeper's own run and decision log |

Every one of these is a diagnostic, not a result; none carries a row. All of them, at every
level, sit on the log side of the split, confirming the answer to question 1.

Today, `log.py` sends all of them, at every level, to 1 `StreamHandler(sys.stdout)`
([`prober/ftg_prober/log.py`](../prober/ftg_prober/log.py) lines 21-30; spec 0003 section 8
says the same: "the lines go to stdout only, so that `journalctl -u ftg-prober` shows them on
a VM"). That is the Twelve-Factor model: write 1 event stream to `stdout`, unbuffered, and let
the execution environment capture, combine, and route it
([12factor.net, Logs](https://12factor.net/logs)). It does not conflict with the
stdout/stderr severity point above (nothing here claims to be a result); it conflicts with the
destination rule, because journald on the VM is the only destination, and a VM
replacement deletes it. systemd's journal does capture a service's
`stdout` and `stderr` as 2 distinct byte streams and splits each on newlines into separate
records, but a local, VM-bound journal is still not object storage, and is still gone on VM
replacement ([`systemd-journald`, man page](https://man.archlinux.org/man/systemd-journald.service.8.html)).

## 3. Recommendation: the 2 streams, their destinations, and the gaps

**The 2 streams and their destinations.** Stream 1, rows, already goes to the data prefix
through `Store.put` with a key built by `build_key`
([`prober/ftg_prober/store.py`](../prober/ftg_prober/store.py) lines 44-66); this note changes
nothing there. Stream 2, the log of this process, keeps going to stdout exactly as today (for
`journalctl` while a human is attached to a live VM), and gains a second handler that buffers
every record and flushes it as 1 JSONL object to the logs prefix through the same `Store.put`,
the design proposes. The key should mirror the row key's shape so the row glob of
the dashboard never matches it: `experiment=<name>/logs/provider=<p>/date=<YYYY-MM-DD>/<ts>_<host>_<uuid>.jsonl`.

**Which levels ship.** Ship whatever the process logger is set to, the same level gate the
stdout handler already uses, so `--log-level` controls both destinations at once; the default
stays INFO (spec 0003 section 8.3), and `--log-level DEBUG` turns on DEBUG for both the store
handler and the suppressed `azure` SDK logger, which already follows the root level at DEBUG
([`prober/ftg_prober/log.py`](../prober/ftg_prober/log.py) lines 32-34). 1 handler, 1 level, 2
destinations: this matches how Python's `logging` module gates records, a logger's effective
level first, then each handler may filter further, but here both handlers should share 1 gate
to avoid a stdout/store mismatch an operator would not expect
([Python docs, Logging Levels](https://docs.python.org/3/library/logging.html#logging-levels)).

**The record fields**, mapped to the OpenTelemetry log data model
([OpenTelemetry, Logs Data Model](https://opentelemetry.io/docs/specs/otel/logs/data-model/)):

| OTel field | This project's field |
|---|---|
| `Timestamp` | the record's UTC time, same `format_ts` style as a row's `ts_utc` ([`record.py`](../prober/ftg_prober/record.py) line 59) |
| `SeverityText` | the level name, DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `Body` | the message, with the provider error text written as returned |
| `Attributes` | the existing `kv()` fields, as a JSON object instead of a formatted string: `probe_type`, `region`, `error`, `key`, `rows`, and so on ([`log.py`](../prober/ftg_prober/log.py) lines 37-45) |
| correlation key | `run_id`, already on most scheduler events, plus `experiment` and `provider`, already fixed per process; a log line without a `run_id` (`run.start`, `scheduler.stopped`) still carries `experiment`/`provider`/`host` |

**File layout, rotation, flush.** 1 buffer per process, flushed every 60 s and at shutdown, 1
flush = 1 `Store.put` call with a fresh key (`Store.put` never overwrites,
[`store.py`](../prober/ftg_prober/store.py) lines 108-111, 143-144), so a flush failure can
retry with the same buffered records and a later key, never the same key twice. No local file
rotation is needed; each flush is already a complete, bounded JSONL object.

**Before the store exists, or while it is down.** `setup_logging` runs in `cli.py` before a
`Store` is opened (opening a store needs parsed args), so early lines, such as an argument
error, stay stdout-only; the store-backed handler attaches once a `Store` object exists. A
failed flush writes 1 stdout line and keeps the records for the next flush, up to a cap, and
never raises into the scheduler loop or delays a due probe, as the design requires; this
is also the existing pattern for a probe's own exception handling
([`scheduler.py`](../prober/ftg_prober/scheduler.py) lines 194-198). A simulated run should
still write its logs to the local store, like its rows, so a `--simulate` run exercises the
same code path.

**The sweeper.** Yes, the same rule applies. `sweeper/azure/sweeper/run.py` and
`function_app.py` already use the stdlib `logging` module
(`logger = logging.getLogger("sweeper")`,
[`sweeper/azure/function_app.py`](../sweeper/azure/function_app.py) line 12), but today that
only reaches whatever the Azure Functions host captures from the process, not object storage.
The sweeper should get the same second handler and flush its own log to a logs prefix the same
way the prober does, by the same rule; the sweeper has no "data" prefix of its own rows, so
its log is its only write under this rule, beyond the resource changes it makes.

## Sources

- [man7.org, `stdout(3)`](https://man7.org/linux/man-pages/man3/stdout.3.html)
- [GNU C Library manual, Standard Streams](https://www.gnu.org/software/libc/manual/html_node/Standard-Streams.html)
- [12factor.net, Logs](https://12factor.net/logs)
- [OpenTelemetry concepts, Signals](https://opentelemetry.io/docs/concepts/signals/)
- [OpenTelemetry, Logs Data Model](https://opentelemetry.io/docs/specs/otel/logs/data-model/)
- [Python docs, Logging Levels](https://docs.python.org/3/library/logging.html#logging-levels)
- [Prometheus docs, Data model](https://prometheus.io/docs/concepts/data_model/)
- [`systemd-journald.service`, man page](https://man.archlinux.org/man/systemd-journald.service.8.html)
- [`prober/ftg_prober/log.py`](../prober/ftg_prober/log.py)
- [`prober/ftg_prober/scheduler.py`](../prober/ftg_prober/scheduler.py)
- [`prober/ftg_prober/store.py`](../prober/ftg_prober/store.py)
- [`prober/ftg_prober/record.py`](../prober/ftg_prober/record.py)
- [`prober/ftg_prober/cli.py`](../prober/ftg_prober/cli.py)
- [`prober/ftg_prober/probes.py`](../prober/ftg_prober/probes.py)
- [`sweeper/azure/function_app.py`](../sweeper/azure/function_app.py)
- [`sweeper/azure/sweeper/run.py`](../sweeper/azure/sweeper/run.py)
- [spec/0003-prober-core-spec.md, section 8](../spec/0003-prober-core-spec.md)
