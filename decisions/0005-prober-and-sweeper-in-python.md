# 0005. The prober and the sweeper are Python

- Status: accepted
- Date: 2026-10-11

## Context

Phase 1 of [spec 0002](../spec/0002-gpu-capacity-tracker-spec.md) needs a prober on 1 VM per cloud and a sweeper as a scheduled function on Azure. Both call cloud APIs, write small JSON objects, and run for 2 weeks. The audience is CNCF developers at a conference. They must read the code fast and run it from a fresh clone.

The starting point of the spec is the AWS [Guidance for EC2 Spot Placement Score Tracker](https://github.com/aws-solutions-library-samples/guidance-for-ec2-spot-placement-score-tracker). Its polling code is Python.

Options: Python, Go, or TypeScript.

## Decision

The prober (`prober/`) and the Azure sweeper (`sweeper/azure/`) are Python 3.12.

Reasons:

1. The reference repo is Python. Its scheduled polling and its config-driven target list carry over with small changes.
2. SDK coverage. [boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html) covers each AWS call of the spec. The [Azure SDK for Python](https://learn.microsoft.com/en-us/azure/developer/python/sdk/azure-sdk-overview) covers Compute, Resource Graph, Blob Storage, and the default credential chain.
3. Azure Functions supports Python with the [v2 programming model](https://learn.microsoft.com/en-us/azure/azure-functions/functions-reference-python), so that the sweeper and the prober share 1 language and 1 toolchain.

Tooling, as an earlier build plan stated: [uv](https://docs.astral.sh/uv/) for the virtual environment and the lock file, [ruff](https://docs.astral.sh/ruff/) for lint and format, [pytest](https://docs.pytest.org/en/stable/) for tests. [mise](https://mise.jdx.dev/) pins Python and uv in `mise.toml`.

Rules:

1. Tests run with no cloud credentials and no network. Use fakes.
2. The package declares the cloud SDKs as extras (`aws`, `azure`). A VM installs only the extra of its cloud.
3. The code imports an SDK inside the function that uses it, so that a missing extra fails only at that call.

## Consequences

- One toolchain for the prober and the sweeper.
- Attendees need Python 3.12 and uv. `make init` installs both through mise.
- Go would give 1 static binary per cloud. We give up this for the smaller distance to the reference repo. This decision can change after the experiment.
