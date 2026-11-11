# Research

The research notes of the project, from public sources. Each note ends with a `## Sources` list of the primary sources that it cites. A note that was edited later also has a `## Revisions` list.

[research/learnings/](learnings/README.md) holds short, public lessons drawn from these notes, for an outside audience.

| Note | Topic | Date |
|---|---|---|
| [Azure capacity signals](azure-capacity-signals.md) | Public signals for Azure GPU capacity before an allocation attempt | 2026-10-11 |
| [AWS capacity signals](aws-capacity-signals.md) | Public signals for AWS GPU capacity before an allocation attempt | 2026-09-25 |
| [AWS Spot placement score per GPU type](aws-spot-placement-score-per-gpu-type.md) | Single-type Spot placement scores for GPU instance types | 2026-10-11 |
| [Azure Spot Placement Score per VM size](azure-spot-placement-score-per-vm-size.md) | Spot placement scores per Azure VM size, and the preview SKU Mix API | 2026-10-11 |
| [AWS and Azure GPU equivalents](aws-azure-gpu-equivalents.md) | Nearest Azure GPU VM size for each AWS GPU instance type, by GPU model, count, and interconnect | 2026-10-11 |
| [GPU lineup on AWS and Azure](gpu-lineup-aws-azure.md) | GPU models on AWS and Azure, and the instance families and VM series for each | 2026-10-11 |
| [AWS capacity signals per instance type](aws-capacity-signals-per-instance-type.md) | Per-type AWS signals for GPU types: Spot price, Spot Advisor, Capacity Block and training plan offerings | 2026-10-11 |
| [API rate limits of the signal probes](api-rate-limits-of-signal-probes.md) | Rate limits, quotas, and retry behavior of the AWS and Azure APIs that the probes call, with a request budget | 2026-10-12 |
| [Azure GPU VM size names](azure-gpu-vm-size-names.md) | How Azure names its GPU VM sizes, with each GPU size name of the config decoded | 2026-10-11 |
| [AWS Spot placement score configuration limit](aws-spot-placement-score-configuration-limit.md) | The size, key, window, and scope of the `GetSpotPlacementScores` configuration limit, and safety rules for a future measurement | 2026-10-12 |
| [Rate limit of the Azure Spot Placement Score API](azure-spot-placement-score-rate-limit.md) | What Microsoft documents about the 429 limit of the Spot Placement Score API, and a public 429 capture | 2026-10-12 |
| [Limits for a single GPU type signal from Spot score requests](single-gpu-type-signal-from-spot-scores.md) | The limits that bound a request design for 1 GPU type on the Spot score APIs | 2026-10-12 |
| [AWS interruptible Capacity Reservations](aws-interruptible-capacity-reservations.md) | What interruptible Capacity Reservations are, who can see or use them, and why they give this project no probe | 2026-10-12 |
| [Results and logs as 2 streams](results-and-logs-streams.md) | The stdout/stderr analogy for probe results vs. log events, an event classification, and a plan for shipping logs to the store | 2026-10-14 |
