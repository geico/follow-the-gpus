# Follow the GPUs

Follow the GPUs measures where and when you can get a GPU machine on AWS and Azure. A prober VM in each cloud asks the cloud for GPU capacity and writes one record for each call. A local board shows the records as heat maps.

It is for engineers who plan GPU capacity, and for people who build a broker that places work in the cloud with free GPUs. The project measures. It does not buy capacity, and it is not a broker.

Status: early. The sample board runs with no cloud. The deploy steps need an AWS or Azure account that you own.

## See the board with no cloud

You need `make`, and either [mise](https://mise.jdx.dev/) or the tools below.

```sh
make init                  # with mise: pins the tools (it runs mise trust). Turns on the git hooks
make web/run                # open http://127.0.0.1:4173/
```

With no rows of your own, the board shows a synthetic sample. The first run needs the network once, for `npm ci`.

mise only pins tool versions. Without it, install OpenTofu, Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, and DuckDB at the versions in `mise.toml`. `make lint` and `make fmt` also need [markdownlint-cli2](https://github.com/DavidAnson/markdownlint-cli2) and [lychee](https://lychee.cli.rs/). For a cloud, add the AWS CLI, or the Azure CLI with `azcopy`. `make` uses the tools on your PATH.

## Run it in your cloud

A stack costs money each month. A probe that holds real capacity costs more. That probe runs only when two keys allow it, and both keys are off by default. Read [cost and consent](docs/cost-and-consent.md) first.

1. Read [the prerequisites](docs/prerequisites.md). You bring a network and credentials.
2. Copy the example settings, and put in your own values. [Config](docs/config.md) explains each one. In the experiment file, set `experiment:` to a dated name ([details](docs/config.md#the-experiment-file)).

   ```sh
   cp terraform/aws/terraform.tfvars.example terraform/aws/terraform.tfvars
   ```

3. Log in to the cloud (`aws sso login`, or `az login`).
4. Run the read-only checks, and request GPU quota on day 1:

   ```sh
   make preflight/aws
   make quota/aws        # a dry run; QUOTA_ARGS=--apply sends the requests
   ```

5. Plan, read the plan, and apply it:

   ```sh
   make plan/aws
   make apply/aws
   ```

6. Make sure that the new release runs: `make status/aws` (a full check: `make verify/aws`).
7. Copy the rows and show the board: `make dev`.

Page by page: [deploy on AWS](docs/deploy-aws.md), [deploy on Azure](docs/deploy-azure.md), [the board](docs/board.md), [troubleshooting](docs/troubleshooting.md).

## Go deeper

| Folder | What it holds |
|---|---|
| [`docs/`](docs/) | [How it works](docs/how-it-works.md), prerequisites, config, and the guides above |
| [`spec/`](spec/README.md) | The requirements. The spec is the source of truth. |
| [`decisions/`](decisions/README.md) | Why each design choice was made |
| [`research/`](research/README.md) | Research notes, with a source for each claim |

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## Disclaimer

This is not an official GEICO product. GEICO does not give support for it.

The deploy steps create resources in your cloud account(s), and you pay for them. Destroy the stack when you do not need it.

Built with help from Claude, which is, ironically, part of the reason GPUs are hard to find.

## License

[Apache-2.0](LICENSE). See [NOTICE](NOTICE).
