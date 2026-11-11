---
title: "Rate limit of the Azure Spot Placement Score API"
date: 2026-10-12
---

# Rate limit of the Azure Spot Placement Score API

## Summary

This note looks for the request limit of the Azure Spot Placement Score API itself, apart from the Azure Resource Manager (ARM) write bucket. It extends [API rate limits of the signal probes](api-rate-limits-of-signal-probes.md) and [Azure Spot Placement Score per VM size](azure-spot-placement-score-per-vm-size.md).

- No public Microsoft source gives a number, a scope, or a window for the limit.
- 1 public report shows a real 429 of this API. Its body is plain text: "You have reached the maximum number of requests allowed. Please try again after 3600 seconds."
- That 429 had no `Retry-After` header and no `x-ms-ratelimit-remaining-resource` header. Its ARM write headers showed 199 and 2999, near full.
- Thus the limit is not the ARM write bucket (inference). It comes from the Spot Placement Score service, a layer in front of it, or the GET call quota that the service uses (inference).
- The number, the scope, and the window stay open. Measure them on the subscription that runs the experiment.
- The probe waits 60 seconds after a 429 and retries 1 time. A wait of 3600 seconds makes that retry fail (inference).

## Scope and method

The search covered these places on 2026-10-12:

- Microsoft Learn: the Spot Placement Score how-to, the Compute throttling limits page, the Compute throttling troubleshooting page, the ARM throttling page, and the CLI and PowerShell references.
- The `Recommender` folder of the azure-rest-api-specs repo, at commit `8da3d37c78b33bbc7204d0b0d0a098925b97bdbe`.
- GitHub issues in the Azure organizations, with searches for "spot placement score" and "placementScores" with 429, throttle, and "rate limit".

The work for this note sent no request to the Spot Placement Score API. Live calls were stopped, because a measurement of one subscription does not carry over to another.

Status of each value:

- **Documented**: a Microsoft page or the Microsoft API spec states it.
- **Reported**: only a third-party page, a forum, or a GitHub issue states it.

## What Microsoft documents

- **The 429 (documented, no number).** The how-to lists "429 | Too many requests | Unable to generate placement score due to hitting a rate limit". It gives no number, no scope, and no window. ([how-to](https://learn.microsoft.com/azure/virtual-machine-scale-sets/spot-placement-score))
- **GET quota (documented).** The how-to says: "Spot Placement Score API internally calls other GET APIs and is part of your GET call quota." It does not name the quota. ([how-to](https://learn.microsoft.com/azure/virtual-machine-scale-sets/spot-placement-score))
- **No Compute policy (documented by absence).** The Compute throttling limits page lists policies for VMs, scale sets, and scale set VMs. It has no policy for Spot Placement Score. ([Compute throttling limits](https://learn.microsoft.com/azure/virtual-machines/compute-throttling-limits))
- **Provider limits (documented).** The ARM page says: "Resource providers apply their own throttling limits." ([ARM throttling](https://learn.microsoft.com/azure/azure-resource-manager/management/request-limits-and-throttling))
- **Compute 429 shape (documented).** The troubleshooting page shows a Compute 429 with a JSON body, code `OperationNotAllowed`, detail code `TooManyRequests`, and a `Retry-After` header. The page does not name Spot Placement Score. ([throttling errors](https://learn.microsoft.com/troubleshoot/azure/virtual-machines/windows/troubleshooting-throttling-errors))
- **API spec (documented by absence).** In the `Recommender` spec, the Spot Placement Score operation has only a `200` response and a `default` error response. The spec has no 429 response and no throttle text. ([RecommenderRP.json 2025-06-05](https://github.com/Azure/azure-rest-api-specs/blob/8da3d37c78b33bbc7204d0b0d0a098925b97bdbe/specification/compute/resource-manager/Microsoft.Compute/Recommender/stable/2025-06-05/RecommenderRP.json))

## A public 429 capture

Issue 28765 in the azure-powershell repo, "Invoke-AzSpotPlacementScore does not handle throttling situation", was opened on 2025-10-28. It is open. It pastes the debug output of 1 throttled call. ([issue 28765](https://github.com/Azure/azure-powershell/issues/28765))

| Item | Value in the capture |
|---|---|
| Request | `POST .../providers/Microsoft.Compute/locations/eastus/placementScores/spot/generate?api-version=2025-06-05` |
| Status | `TooManyRequests` (429) |
| Body | "You have reached the maximum number of requests allowed. Please try again after 3600 seconds." |
| Body format | Plain text, not JSON. The cmdlet fails with "Expected '{' or '['. Was String: You." |
| `Retry-After` | Not in the header list |
| `x-ms-ratelimit-remaining-resource` | Not in the header list |
| `x-ms-ratelimit-remaining-subscription-writes` | 199 |
| `x-ms-ratelimit-remaining-subscription-global-writes` | 2999 |
| Other headers | `Cache-Control`, `Pragma`, `x-ms-operation-identifier`, `Strict-Transport-Security`, `x-ms-request-id`, `x-ms-correlation-request-id`, `x-ms-routing-request-id`, `X-Content-Type-Options`, `X-Cache`, `X-MSEdge-Ref`, `Date` |

The capture does not say how many calls came before the 429, or over what time.

### What the capture shows

- **Not the ARM write bucket (inference).** The ARM write bucket is 200 with a refill of 10 per second. The 429 had 199 writes left. Thus the ARM bucket did not throttle the call. ([ARM throttling](https://learn.microsoft.com/azure/azure-resource-manager/management/request-limits-and-throttling))
- **Not a Compute policy (inference).** A Compute policy sends `x-ms-ratelimit-remaining-resource` and a JSON body. The capture has neither. ([throttling errors](https://learn.microsoft.com/troubleshoot/azure/virtual-machines/windows/troubleshooting-throttling-errors))
- **Wait of 1 hour (reported).** The body asks for a wait of 3600 seconds. This can be a window of 1 hour, or a fixed lockout after a count. The capture does not show which.
- **No `Retry-After` (reported).** A client that reads only `Retry-After` gets no wait value. With default settings, azure-core retries a POST on a 429 only when the response has a `Retry-After` header ([_retry.py](https://github.com/Azure/azure-sdk-for-python/blob/azure-core_1.41.0/sdk/core/azure-core/azure/core/pipeline/policies/_retry.py#L202-L241)). Thus azure-core with default settings does not retry this 429 (inference).

## Effect on the probes

- **Retry (inference).** Spec 0009 section 2.6 waits 60 seconds after a 429 and sends the request 1 more time ([spec 0009](../spec/0009-azure-spot-signals-spec.md)). If the 3600-second wait applies, that retry fails too. The runs of the next hour fail also. Each failed attempt is 1 more request.
- **Error code (inference).** The plain-text body has no `error.code`. Under rule 6 of section 2.6, the `error_code` of such a row is `HTTP 429` ([spec 0009](../spec/0009-azure-spot-signals-spec.md)).
- **Budget.** Keep 1 request for each configuration each 15 minutes or more, as the cache guidance of the how-to says ([how-to](https://learn.microsoft.com/azure/virtual-machine-scale-sets/spot-placement-score)). Do not raise the rate to find the limit.

## Places with no number

- Microsoft Learn: the how-to, the Compute throttling pages, the ARM throttling page, the CLI reference, and the PowerShell reference.
- azure-rest-api-specs: all files outside `examples/` in the `Recommender` folder at commit `8da3d37c78b33bbc7204d0b0d0a098925b97bdbe`, for the terms 429, throttl, "rate limit", "too many", and retry.
- GitHub issue search: the searches of this note found no issue other than issue 28765 that shows a Spot Placement Score 429 (not verified: GitHub search is not complete).
- Not checked: the SDK source for Python, Go, and .NET; the azure-cli and azure-powershell source trees; and a Microsoft support case.

## Open items

- The number, the scope, and the window of the limit. Measure them on your own subscription, from the probe 429s or a small, spaced check.
- The probe action after a 429 that asks for a wait of 3600 seconds.

## Sources

- <https://learn.microsoft.com/azure/virtual-machine-scale-sets/spot-placement-score>
- <https://learn.microsoft.com/azure/virtual-machines/compute-throttling-limits>
- <https://learn.microsoft.com/troubleshoot/azure/virtual-machines/windows/troubleshooting-throttling-errors>
- <https://learn.microsoft.com/azure/azure-resource-manager/management/request-limits-and-throttling>
- <https://github.com/Azure/azure-rest-api-specs/blob/8da3d37c78b33bbc7204d0b0d0a098925b97bdbe/specification/compute/resource-manager/Microsoft.Compute/Recommender/stable/2025-06-05/RecommenderRP.json>
- <https://github.com/Azure/azure-powershell/issues/28765>
- <https://github.com/Azure/azure-sdk-for-python/blob/azure-core_1.41.0/sdk/core/azure-core/azure/core/pipeline/policies/_retry.py>
- [API rate limits of the signal probes](api-rate-limits-of-signal-probes.md)
- [Spec 0009, Azure Spot signals](../spec/0009-azure-spot-signals-spec.md)
