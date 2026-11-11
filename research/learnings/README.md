# Learnings

This folder holds short, public lessons about cloud GPU capacity APIs. Each
file is 1 brief about 1 lesson. The briefs are the raw material for a talk
to an outside audience. They stay small on purpose: a reader must get the
point from 1 file, without the rest of this repo.

A brief states a fact that the provider docs do not state well, or a limit
that the docs do not give a number for. It is not a tutorial, and it does
not repeat what the provider docs already explain well.

Each brief uses Simple English, the plain-writing style of ASD-STE100
(Simplified Technical English), in pragmatic mode. This project's
`simple-english` skill follows the same rules. Core rules:

- Short sentences, about 20 to 25 words.
- 1 fact per sentence.
- Active voice.
- No `should`, `may`, `might`, or `could`. Write `can` or `must`
  instead.
- No semicolons. Write 2 sentences instead.
- A concrete example for each claim.

## Rule: never invent a fact

Every claim in a brief needs 1 of these 2 things:

1. A link to a primary source (an AWS or Azure doc page first).
2. A dated observation that this repo already records, in `research/`
   or in `spec/`.

When the provider docs say nothing about a point, the brief says
"not documented" and links the page where the fact belongs. A brief never
states a number that has no source.

## The template

Each brief is 1 Markdown file with this front matter and these sections.

```markdown
---
title: "A claim, stated as a fact"
date: YYYY-MM-DD
---

# A claim, stated as a fact

## What the docs say

A short quote from the provider doc, with a link. When the docs say
nothing, write "Not documented" and link the page where the fact belongs.

## What we saw

The exact call or command we sent, the date (UTC), and the raw answer.
State the source: a live call, a public dataset, or a note already in
this repo.

## Example

A concrete request and its answer. Use the exact CLI command or API
parameters, or a table of observed values with dates (UTC).

## Why it matters

1 to 3 sentences. What a reader must change because of this fact.

## Sources

A list of links, in the order that the text cites them.
```

A file name states the lesson, for example
`capacity-block-starts-round-to-1130-utc.md`. One lesson per file.

## Index

| Brief | Date |
|---|---|
| [A Capacity Block search rounds its start to now + 30 minutes or to 11:30 UTC](capacity-block-starts-round-to-1130-utc.md) | 2026-10-13 |
| [A wide Capacity Block search returned the earliest offer every time: AWS promises only the lowest price](capacity-block-search-returns-earliest-not-cheapest.md) | 2026-10-13 |
| [A longer Capacity Block started up to 15 days later, never earlier](capacity-block-earliest-start-depends-on-length.md) | 2026-10-13 |
| [A single GPU instance type can score 9 on the AWS Spot placement score](aws-spot-score-single-gpu-type-can-score-9.md) | 2026-10-11 |
| [AWS gives no number for the Spot placement score configuration limit](aws-spot-score-configuration-limit-has-no-number.md) | 2026-10-12 |
| [An interruptible Capacity Reservation is visible only to a shared account](aws-interruptible-capacity-reservation-needs-a-share.md) | 2026-10-12 |
| [The Azure Spot Placement Score has no 3-size rule](azure-spot-score-has-no-3-size-rule.md) | 2026-10-11 |
| [Azure's isQuotaAvailable field can flip from true to false as you raise the VM count](azure-isquotaavailable-changes-with-vm-count.md) | 2026-10-12 |
| [A Spot placement score mix control must use 1 GPU class, not 3](aws-spot-score-gpu-trio-must-share-1-gpu-class.md) | 2026-10-13 |
| [Pair a single type with a same-class mix, to test for the Spot score penalty](aws-spot-score-pair-single-with-mix-to-test-for-penalty.md) | 2026-10-13 |
| [Azure docs do not say that What-If or validate check on-demand GPU capacity](no-doc-says-azure-what-if-or-validate-checks-on-demand-capacity.md) | 2026-10-14 |
| [Azure's RestrictedSkuNotAvailable can flip within hours](azure-restricted-sku-can-flip-within-hours.md) | 2026-10-15 |
| [Most Azure A10 and A100 GPU Spot prices sat at 18.5 % of pay-as-you-go](azure-most-a10-and-a100-spot-prices-sit-at-18-5-percent.md) | 2026-10-15 |
| [Azure Flexible scale sets reject singlePlacementGroup=true with OperationNotAllowed](azure-flexible-scale-set-rejects-single-placement-group.md) | 2026-10-16 |
| [az vm list-skus leaves out restricted sizes unless you pass --all](azure-cli-list-skus-hides-restricted-sizes.md) | 2026-10-16 |
| [AWS Spot price records were stamped on the hour, about 6 hours apart per zone](aws-spot-price-records-were-on-the-hour-in-our-window.md) | 2026-10-15 |
| [A Flexible scale-set member lists its NIC before the NIC exists](azure-flexible-scale-set-member-lists-nic-before-it-exists.md) | 2026-10-16 |
| [The Azure capacity reservation docs give no billing granularity, so we do not know the minimum charge](azure-capacity-reservation-billing-granularity-not-documented.md) | 2026-10-16 |
| [A failed zip deploy of an Azure Function App can leave a clean plan and old code](azure-failed-zip-deploy-can-leave-a-clean-plan.md) | 2026-10-17 |
| [Azure Retail Prices returns Spot and Low Priority items under the same armSkuName](azure-retail-prices-spot-and-low-priority-share-armskuname.md) | 2026-10-17 |
| [A low score for a 3-type CPU request can be real Spot shortage](aws-spot-score-cpu-trio-low-score-can-be-real-shortage.md) | 2026-10-27 |

Read [research/README.md](../README.md) for the full list of research notes.
