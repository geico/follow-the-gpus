# How it works

This page explains what the probers measure, how a probe round runs, and what the board shows. The specs are the source of truth. Each claim links to its spec section.

## The question

A cloud region can refuse a request for a GPU machine ([spec 0001](../spec/0001-project-description.md), "The problem"). A future broker could search many regions and clouds and start a node where capacity exists.

This project does not build the broker. It measures the facts that the broker needs: where and when a person can get a GPU, on AWS and on Azure, in the continental US ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), the "Purpose" line at the top). The project does not purchase capacity ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), "Out of scope"). Provisioning probes (see below) hold capacity for a short time, and only when the consent keys allow it.

## Two kinds of probe

Each data row records one attempt or poll of a probe. A signal probe writes one file for each call. A provisioning probe writes one file for each finished entry (see step 6 below). The rows of one call share a `run_id`. The `probe_type` field says which probe wrote a row ([spec 0003](../spec/0003-prober-core-spec.md), section 4.1).

A **signal probe** reads a number that a provider publishes. It creates nothing and needs no GPU quota. Examples are the Spot placement score, Spot price history, the Spot interruption feed, and Capacity Block offerings ([spec 0008](../spec/0008-aws-probes-spec.md), [spec 0009](../spec/0009-azure-spot-signals-spec.md)).

A **provisioning probe** asks the cloud for real capacity and then gives it back. It is the only direct test of "can I get this machine now". The probes are an On-Demand Capacity Reservation on AWS, and a capacity reservation or a scale set on Azure ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), sections 4.4, 4.5, and 4.9).

Two rules apply to all provisioning probes:

- The request is all or nothing.
  - If the cloud places fewer machines than asked, the attempt fails.
  - A `no_capacity` failure halves the count for the next attempt.
  - Any outcome other than success or `no_capacity` ends the search.
  - After a failure, a binary search runs between the last success and the last failure.
  - After a full success, an entry with `n_max` goes up in steps. An entry without `n_max` stops.

  Sources: [spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), section 2, D8; [ADR 0009](../decisions/0009-allocation-probes-are-all-or-nothing-with-halving.md); [ADR 0023](../decisions/0023-the-provisioning-search-goes-up-after-a-full-success.md); [spec 0010](../spec/0010-azure-provisioning-probes-spec.md), section 5, step 7.
- The probe never waits for the machine to boot. It tears down as soon as the outcome is known ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), section 2, D7).

## A probe round

The cadence of the provisioning probes comes from the `cadence` key of the experiment file. The files in [config/examples](../config/examples/README.md) show the values. Each signal probe has its own cadence; for example 5 minutes for the Spot price and 24 hours for the Spot advisor ([spec 0003](../spec/0003-prober-core-spec.md), section 6, rule 1; [spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), section 2, D2). One round of provisioning probes works like this:

1. At start, the prober loads the experiment file. The file lists the regions, sizes, and jobs ([spec 0003](../spec/0003-prober-core-spec.md), section 3).
2. A read-only pre-check runs first. On Azure, it checks restrictions and quota ([spec 0010](../spec/0010-azure-provisioning-probes-spec.md), section 4). On AWS, it lists the zones that offer the size and reads quota ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), section 4.9, step 2). It creates nothing.
3. The probe asks for the machines, in one lane for each region ([ADR 0020](../decisions/0020-provisioning-probes-run-in-region-lanes-off-the-signal-thread.md)).
4. The probe records the outcome: for example `success`, `no_capacity`, or `quota`.
5. The probe removes what it created, and makes sure that nothing remains.
6. The prober writes the rows of each finished entry to the store, one record for each attempt ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), section 7.1; [ADR 0020](../decisions/0020-provisioning-probes-run-in-region-lanes-off-the-signal-thread.md), section 4).

One small prober VM runs in each cloud. It uses the identity of the VM, so no stored secret exists ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), section 2, D12).

## Cost and consent

A provisioning probe can cost money while it holds capacity, so two controls guard it. To run for real, a non-simulated entry of `provisioning_probes` must say `allow_billable_provisioning: true`. The stack must also set `grant_billable_provisioning`, which defaults to off. Nothing creates real capacity unless both say yes. [Cost and consent](cost-and-consent.md) shows how to set them ([ADR 0015](../decisions/0015-two-independent-controls-replace-the-terraform-allocation-guard.md), [ADR 0016](../decisions/0016-rename-the-consent-key-to-allow-billable-provisioning.md)).

A sweeper in each cloud runs every minute. It removes any probe resource that is older than a few minutes, so a failed probe cannot bill for long ([spec 0007](../spec/0007-sweeper-spec.md), [spec 0013](../spec/0013-aws-sweeper-spec.md)). A job with `simulate: true` makes no real call ([ADR 0011](../decisions/0011-store-and-per-job-simulate-replace-run-mode.md)).

## Where data and logs go

Each cloud writes to its own object store: S3 on AWS, Blob storage on Azure ([spec 0002](../spec/0002-gpu-capacity-tracker-spec.md), section 2, D13; [spec 0003](../spec/0003-prober-core-spec.md), section 9). The prober and the sweepers write there. The store is the source of truth. Daily files in the store and the mirror on your computer (see below) are copies.

A **data row** is one record for each probe attempt or poll. It holds the time, region, size, requested and placed counts, outcome, and a `details` object ([spec 0003](../spec/0003-prober-core-spec.md), section 4.1). One file (a `.jsonl` file) holds the rows of one probe call, or of one finished entry for a provisioning probe, one JSON object on each line.

A file has this key ([spec 0003](../spec/0003-prober-core-spec.md), section 4.2):

```text
experiment=<name>/provider=<p>/probe_type=<t>/date=<YYYY-MM-DD>/<ts>_<target>_<uuid>.jsonl
```

**Logs** are a separate stream. The prober and the sweepers write their own log lines to the same store, under a `logs` prefix. Rows and logs never mix ([spec 0003](../spec/0003-prober-core-spec.md), section 8.2). The prober never filters a log line. So a log can hold an account ID, a subscription ID, or a resource name. Read the logs before you share them. A provider error message goes to the log, not to the row ([spec 0003](../spec/0003-prober-core-spec.md), section 8.1).

A daily job on the prober VM packs each closed day into one compressed file for each probe type, with a manifest. The raw objects stay the source of truth ([spec 0003](../spec/0003-prober-core-spec.md), sections 4.3 and 9B).

## What the board shows

The board is one page on your computer. It answers one question: "Can I get this GPU machine now, anywhere, and if not, what do I do?" ([spec 0006](../spec/0006-dashboard-spec.md), introduction).

The board does not read the stores. First, `make data/sync` copies the rows from the stores to a local folder. It uses your own cloud login. Then a local DuckDB is built from that copy. The board server reads only that file and holds no cloud credential ([spec 0006](../spec/0006-dashboard-spec.md), section 1, item 2, and sections 4.3 and 5.5). The board computes every number and caption from the rows in view. A static export writes the same data as files, and the board works with no server ([spec 0006](../spec/0006-dashboard-spec.md), sections 8 and 9).

Each line of the board is a machine, such as H100 x8 or L4. Machines sit in tiers, from frontier nodes to small cards ([spec 0006](../spec/0006-dashboard-spec.md), section 11.1). The sections run top to bottom, from "Right now" to the definitions ([spec 0006](../spec/0006-dashboard-spec.md), section 11). [The board](board.md) lists the question that each section answers.

The board drops simulated rows. It shows them, with a banner, only when the store holds no real rows ([spec 0006](../spec/0006-dashboard-spec.md), section 5.2).

## Design records

These folders explain the design. They are not a user guide.

- [spec/](../spec/README.md): the exact behavior of each part.
- [decisions/](../decisions/README.md): why the project is built this way.
- [research/](../research/README.md): the sourced notes behind the probes. Short lessons are in [research/learnings/](../research/learnings/README.md).
