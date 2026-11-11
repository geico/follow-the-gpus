# ftg-prober

The prober core of the GPU capacity tracker. The spec is [spec/0003-prober-core-spec.md](../spec/0003-prober-core-spec.md).

## Use

From the repo root:

```sh
make prober/test        # run the tests
make prober/lint        # ruff check and ruff format --check
make prober/sim         # write 24 hours of synthetic rows to data/sim/
```

In this folder, `uv run ftg-prober --help` shows the command line.

## Layout

- `ftg_prober/` is the package. The core modules are `cli`, `config`, `record`, `store`, `probes`, `scheduler`, `sim`, `log`, and the daily compaction (`compact`, `compact_job`, `compact_stores`; spec 0003 section 9B).
- `ftg_prober/aws/` and `ftg_prober/azure/` hold 1 module per probe. The core loads them.
- `tests/` runs with no cloud credentials and no network.

## No probes for a provider

`ftg-prober run --provider <p>` loads the probe modules under `ftg_prober/<p>/`. If it finds 0 probes for the provider, it logs `run.start` with `probes=0` and exits at once with code 0. This is expected until the probe modules of that provider exist.

On a VM, the systemd service `ftg-prober` then stops with exit code 0. The AWS unit restarts only on a failure, so the service stays stopped. The Azure unit restarts every 30 seconds. A deploy after the probe modules exist installs the new code ([terraform/aws/README.md](../terraform/aws/README.md#update-the-code), [terraform/azure/README.md](../terraform/azure/README.md#notes)).
