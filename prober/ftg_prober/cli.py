"""The `ftg-prober` command line. Spec 0003 section 2.

Exit codes: 0 on a clean stop, 1 when `compact` failed a day, 2 on a config error or a usage error.
"""

import argparse
import logging
import os
import signal
import sys
import threading
from collections.abc import Sequence

from ftg_prober import __version__
from ftg_prober.compact_job import compact_all
from ftg_prober.compact_stores import open_compact_store
from ftg_prober.config import ConfigError, load_config
from ftg_prober.log import attach_store_handler, kv, setup_logging
from ftg_prober.probes import load_probes, packages_for
from ftg_prober.scheduler import Scheduler
from ftg_prober.sim import write_history
from ftg_prober.store import FileStore, open_store

log = logging.getLogger("ftg_prober.cli")

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ftg-prober", description="GPU capacity tracker prober.")
    parser.add_argument("--version", action="version", version=f"ftg-prober {__version__}")
    parser.add_argument(
        "--log-level", default="INFO", help="DEBUG, INFO, WARNING, or ERROR (default INFO)"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the probes of 1 provider at their cadence")
    run.add_argument("--provider", required=True, choices=("aws", "azure"))
    run.add_argument("--config", required=True, help="path of targets.yaml")
    run.add_argument(
        "--store",
        required=True,
        help="file:///abs/path, s3://bucket[/prefix], or az://account/container[/prefix]",
    )
    run.add_argument("--once", action="store_true", help="run each probe 1 time, then exit")
    run.add_argument("--probe", default=None, help="run only the probe with this probe_type")
    run.add_argument(
        "--simulate",
        action="store_true",
        help="simulate every job of this run; refuses a --store that is not file://",
    )

    comp = sub.add_parser(
        "compact",
        help="pack each closed UTC day into 1 zstd JSONL file per probe type (spec 0003 9B)",
    )
    comp.add_argument("--provider", required=True, choices=("aws", "azure"))
    comp.add_argument("--config", required=True, help="path of targets.yaml")
    comp.add_argument("--store", required=True, help="file:///abs/path or s3://bucket[/prefix]")

    simp = sub.add_parser("sim", help="write synthetic, simulated rows for every probe type")
    simp.add_argument("--config", required=True, help="path of targets.yaml")
    simp.add_argument(
        "--store",
        required=True,
        help="a file:///abs/path store URL, for example file:///abs/path/data/sim",
    )
    simp.add_argument(
        "--hours", required=True, type=int, help="hours of history to write, ending now"
    )
    simp.add_argument("--seed", type=int, default=42, help="random seed (default 42)")

    check = sub.add_parser(
        "check-config", help="validate targets.yaml; exit 2 with the faults on an error"
    )
    check.add_argument("path", help="path of targets.yaml")
    check.add_argument(
        "--provider",
        choices=("aws", "azure"),
        default=None,
        help="also do what `run --provider` does up to its first network call: require this "
        "provider's section and load its probes (ADR 0012 section 4, rule 4, the VM-side gate)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        setup_logging(args.log_level)
        if args.command == "check-config":
            return cmd_check_config(args.path, args.provider)
        if args.command == "sim":
            return cmd_sim(args)
        if args.command == "compact":
            return cmd_compact(args)
        return cmd_run(args)
    except (ConfigError, ValueError, LookupError) as exc:
        print(f"ftg-prober: config error: {exc}", file=sys.stderr)
        return EXIT_CONFIG


def cmd_check_config(path: str, provider: str | None = None) -> int:
    config = load_config(path)
    if provider is not None:
        # ADR 0012 section 4, rule 4: everything `cmd_run` does before its first network call,
        # so a config with no `provider:` section, or a probe plugin that fails to load or
        # construct, fails this gate the same way `run --provider` would, with no network call
        # on either side of this line.
        config.section(provider)
        probes = load_probes(packages_for(provider))
        scheduler = Scheduler(
            probes, config=config, provider=provider, store=FileStore("/dev/null")
        )
        scheduler.select(None)
    providers = [p for p in ("aws", "azure") if getattr(config, p) is not None]
    print(f"{path}: ok (experiment={config.experiment}, providers={','.join(providers)})")
    return EXIT_OK


def _require_file_store(store: object, flag: str) -> None:
    """ADR 0011 section 2: a fully simulated run can only write to a local folder."""
    if not isinstance(store, FileStore):
        raise ValueError(f"{flag} refuses a --store that is not file://; use file:///abs/path")


def cmd_sim(args: argparse.Namespace) -> int:
    if args.hours < 1:
        raise ValueError("--hours must be 1 or more")
    config = load_config(args.config, force_simulate=True)
    store = open_store(args.store)
    _require_file_store(store, "sim")
    log.info("sim.start %s", kv(store=store.url, hours=args.hours, seed=args.seed))
    counts = write_history(config, store, args.hours, seed=args.seed)
    total_rows = sum(c["rows"] for c in counts.values())
    total_calls = sum(c["calls"] for c in counts.values())
    log.info("sim.done %s", kv(objects=total_calls, rows=total_rows, store=store.url))
    return EXIT_OK


def cmd_compact(args: argparse.Namespace) -> int:
    config = load_config(args.config)
    config.section(args.provider)
    compact_store = open_compact_store(args.store)
    # The logs go through the normal store, as in `run`: new keys under experiment=<name>/logs/.
    log_handler = attach_store_handler(open_store(args.store), config.experiment, args.provider)
    try:
        summary = compact_all(compact_store, experiment=config.experiment, provider=args.provider)
        return EXIT_FAILED if summary.failed else EXIT_OK
    finally:
        log_handler.close()
        logging.getLogger().removeHandler(log_handler)


def release_id_of(config_path: str) -> str:
    """The release ID of the build that holds `config_path`, or "none" outside a release.

    ADR 0012 section 4: `sync.sh` builds a release in `/opt/ftg/releases/<release ID>/`, where the
    release ID is the sha256 of the manifest, and the unit passes `--config
    /opt/ftg/current/config/...`. `current` is a symlink to that folder, so the real path of the
    config file names the release ID. Only a folder segment counts: a file directly in a `releases`
    folder gives "none".
    """
    parts = os.path.realpath(config_path).split(os.sep)
    for i in range(len(parts) - 3, 0, -1):
        if parts[i] == "releases" and parts[i + 1]:
            return parts[i + 1]
    return "none"


def cmd_run(args: argparse.Namespace) -> int:
    config = load_config(args.config, force_simulate=args.simulate)
    config.section(args.provider)
    store = open_store(args.store)
    if args.simulate:
        _require_file_store(store, "run --simulate")
    log_handler = attach_store_handler(store, config.experiment, args.provider)
    try:
        probes = load_probes(packages_for(args.provider))
        scheduler = Scheduler(
            probes,
            config=config,
            provider=args.provider,
            store=store,
            simulate=args.simulate,
        )
        scheduler.select(args.probe)
        log.info(
            "run.start %s",
            kv(
                provider=args.provider,
                store=store.url,
                probes=len(scheduler.probes),
                once=args.once,
                simulate=args.simulate,
                experiment=scheduler.experiment,
                release=release_id_of(args.config),
            ),
        )
        if args.once:
            scheduler.run_once(args.probe)
            return EXIT_OK

        # An Event wakes the sleep at once on SIGINT or SIGTERM. A plain time.sleep would resume.
        stop_event = threading.Event()
        scheduler.sleep = stop_event.wait

        def _stop(signum: int, _frame: object) -> None:
            log.info("run.signal %s", kv(signal=signal.Signals(signum).name))
            stop_event.set()

        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        scheduler.run_forever(args.probe, stop=stop_event.is_set)
        return EXIT_OK
    finally:
        log_handler.close()
        logging.getLogger().removeHandler(log_handler)
