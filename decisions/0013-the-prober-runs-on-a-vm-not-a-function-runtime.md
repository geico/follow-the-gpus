# 0013. The prober runs on a VM, not a function runtime

- Status: accepted
- Date: 2026-10-19

## Context

A review earlier in the project chose a long-running VM for the prober, over a function
(FaaS) runtime such as AWS Lambda or Azure Functions. This ADR writes down what the
prober needs from its runtime, the options, and the reasons for the pick.

### What the prober needs from its runtime

Requirements 1 and 2 apply to the AWS prober only: only the 3 AWS Capacity Block probes
(`aws_cb_offering`, `aws_cb_earliest`, `aws_cb_longest`) implement `plan` and use the queue
(spec 0003, section 6A), and the queue settings are `aws.queue_*` keys. No Azure probe has a
plan, so the Azure prober puts no call in the queue. Requirements 3 and 4 apply to both clouds.
The provisioning probes of both clouds (`aws_odcr` on AWS; `az_capacity_reservation` and
`az_vmss_flex` on Azure) also run a search, the halving search of
[ADR 0009](0009-allocation-probes-are-all-or-nothing-with-halving.md), section 2, and the
upward search of [ADR 0023](0023-the-provisioning-search-goes-up-after-a-full-success.md), but
that search is not requirement 2: it runs to its end inside 1 lane call, under a deadline per
entry (requirement 3).

1. **A call queue and a token bucket that live for a whole wake cycle (AWS).** The queue and the
   token bucket of each Region live only in the memory of the running process
   ([`prober/ftg_prober/callqueue.py`](../prober/ftg_prober/callqueue.py)). A queued probe adds
   its calls to this queue. At each wake of a Region, the worker takes from that Region's queue
   at most `aws.queue_max_calls_per_wake` calls (default 1), and no more than the whole tokens
   of that Region's bucket (`Scheduler.work` in
   [`prober/ftg_prober/scheduler.py`](../prober/ftg_prober/scheduler.py)). The calls are thus
   spread over many wakes, so that the queue "does not block a 5-minute probe" (spec 0003,
   section 6A, rule 3).
   A restart ends both: "the next process starts with an empty queue... and a bucket at
   `START_TOKENS` tokens" (spec 0003, section 6A, rule 8). [ADR 0012](0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md)
   already accepts this restart cost for a code or a config update, and bounds it to "the rest
   of 1 cycle," because the queue and the bucket are process memory, not a store.
2. **A search that runs in steps across many queued calls (AWS).** `aws_cb_longest` makes up
   to 19 calls per (Region, type) to find the first free day of a Capacity Block and the
   longest block there (spec 0008, section 3B.2, section 3B.5). Each step is 1 queued call, so
   at the default wake of 30 s with a jitter of 20 percent (`QUEUE_DEFAULTS` in
   [`scheduler.py`](../prober/ftg_prober/scheduler.py)) 1 search alone in its Region's queue
   takes about 9.5 minutes on average (19 calls x 30 s), at most about 11.4 minutes (19 calls x
   36 s, the top of the ±20 percent jitter), with no throttle backoff; the whole queue of 1
   Region, up to 110 worst-case calls with the repo config, drains in about 55 minutes on
   average, at most about 66 (110 calls x 36 s) (spec 0008, section 3B.5). Each step's whole state, "the type, the
   current phase, the phase's own small state, the earliest offer found so far, the longest
   offer found so far, and the steps taken, travels in `Call.args`" (spec 0008, section 3B.2),
   and a step writes no row until the search ends (spec 0003, section 6A, rule 5). A restart
   mid-search loses it entirely and restarts it from the first step (spec 0003, section 6A,
   rule 8).
   This is tolerable only because the search lives inside 1 long-running process between its
   own steps, not because it survives a restart.
3. **A scheduler loop that wakes, calls, and sleeps on its own clock.** The scheduler runs
   every selected probe on its own cadence, in 1 loop, and only checks the stop flag between 1
   wake's work and the next (spec 0003, section 6, rules 2 and 3; section 6A, rule 8). A probe
   with a 2-hour cadence and a probe with a 5-minute cadence share this 1 loop and its 1 set of
   in-memory state. On both clouds, the lane probes (`aws_odcr` on AWS; `az_capacity_reservation`,
   `az_vmss_flex`, and `az_precheck` on Azure; spec 0003, section 5.1, rule 7) run in Region
   lanes, 1 thread per Region started by this loop, off the signal thread
   ([ADR 0020](0020-provisioning-probes-run-in-region-lanes-off-the-signal-thread.md),
   spec 0003, section 6B). The lanes are threads of the same process. Each lane call holds the
   lock of its Region, 1 lock per Region shared by all lane probes of the process, for the whole
   call (`region_locks` in [`scheduler.py`](../prober/ftg_prober/scheduler.py); spec 0003,
   section 6B, rule 2). On Azure, each entry also holds a module-global lock per (Region, SKU)
   from its pre-check to its verified teardown (`series_lock` in
   [`azure/prechecks.py`](../prober/ftg_prober/azure/prechecks.py)). A lane call runs the
   entries of its Region in series. On both clouds, a provisioning probe's lane call runs each
   entry's halving or upward search to its end
   under a deadline per entry of (1 + up steps) x (probe timeout + teardown timeout), 150 s per
   step at the defaults of 90 s and 60 s
   ([`azure/prechecks.py`](../prober/ftg_prober/azure/prechecks.py),
   [`aws/odcr.py`](../prober/ftg_prober/aws/odcr.py)). These threads and locks are process
   memory, so they add to the need for 1 long-running process.
4. **A restart that the design already prices in.** ADR 0012 keeps the VM, and removes only
   the extra restart a Terraform-forced VM replacement used to add on every code or config
   change: a systemd timer on the VM pulls a new build and runs `systemctl restart
   ftg-prober.service`, "a few seconds" of downtime (ADR 0012, section 2). What a restart costs
   differs by cloud:
   - On AWS, a restart empties the queue and the bucket and loses a Capacity Block search in
     progress, the same as it always did. ADR 0012 accepts this loss as "the rest of 1 cycle"
     and explicitly rejects persisting the queue (ADR 0012, section 2, option 4) as a feature
     the evidence does not call for.
   - On both clouds, the restart sends SIGTERM first. Each Region lane then finishes only its
     attempt in progress, with its teardown, writes its rows, and starts no new attempt and no
     new entry (spec 0003, section 6B, rule 6). The rest of that lane's cycle is missed until
     the next slot (spec 0003, section 6, rule 2). Only a SIGKILL after the stop timeout, or a
     power loss, ends an entry before its teardown. Then the lane loses at most that entry
     (spec 0003, section 6B, rule 4), and the sweeper of that cloud deletes or cancels the
     probe resource that is left (spec 0007, section 1; spec 0013, section 1).
   - On Azure, there is no queue to empty: for the provisioning probes, a restart costs the
     lane cycle in progress, as above.

## Decision

### 1. Options considered

| # | Option | What matters here |
|---|---|---|
| 1 | A VM with systemd (today) | A single process holds its state for as long as it runs: on AWS, the queue, the bucket, and a Capacity Block search in progress; on both clouds, the scheduler loop and its Region lanes with their locks (section above). A restart, from a deploy, a crash, or a reboot, costs on AWS "the rest of 1 cycle" of the queue (ADR 0012), and on both clouds the lane cycle in progress (requirement 4). |
| 2 | AWS Lambda or Azure Functions | An invocation can start in a fresh execution environment, and both platforms tell an author not to rely on state between invocations (section 2). On AWS, the call queue, the token bucket, and a Capacity Block search do not survive between invocations unless rebuilt from an external store on every invocation. On both clouds, the same holds for 1 scheduler loop with its Region lanes and their locks. |
| 3 | A container service (AWS ECS/Fargate, Azure Container Apps) | A container can run as a long-lived process, like the VM, but adds an orchestration layer (a cluster, a task definition or a container app, a registry) the VM does not need. |

### 2. The facts that matter, by option

**AWS Lambda.**

- **Maximum run time:** a Lambda function invocation runs for at most 900 seconds (15
  minutes); for functions on Lambda Managed Instances, asynchronous invocations and event
  source mapping invocations (except Amazon MQ and Amazon DocumentDB) allow up to 5,400
  seconds (90 minutes)
  ([Lambda quotas: "Function timeout"](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html)).
  1 `aws_cb_longest` search alone, about 9.5 minutes on average and at most about 11.4
  (requirement 2), fits inside 15 minutes. The whole queue of 1 Region, about 55 minutes on average (at most about 66) with the repo
  config (spec 0008, section 3B.5), does not fit inside 15 minutes, but it fits inside the 90-minute limit. An
  `aws_odcr` lane call runs the entries of its Region in series, each under its own deadline
  (requirement 3): for a real (not simulated) entry with `n: 2` and `n_max: 16`, as in the repo
  config
  ([`config/targets.yaml`](../config/targets.yaml)), 1 entry's deadline is 4 x 150 s = 600 s,
  so 1 entry fits in 15 minutes and a lane call of 2 or more such entries can pass 15 minutes.
  Thus the cap alone does not decide on AWS; the stateless design in the next bullet does. In
  each case, Lambda gives no second invocation that remembers where the first one stopped.
- **State between invocations:** "Lambda is designed for short-lived compute tasks that do not
  retain or rely upon state between invocations"
  ([Lambda quotas](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html)).
  The call queue, the token bucket, and a Capacity Block search's `Call.args` are exactly the
  state this line rules out, and so are the scheduler loop and its `aws_odcr` Region lanes
  (requirement 3). AWS does offer a separate feature, Lambda durable functions, that checkpoints
  workflow state across invocations for up to 1 year
  ([AWS Fargate or AWS Lambda?](https://docs.aws.amazon.com/decision-guides/latest/fargate-or-lambda/fargate-or-lambda.html)),
  but that is a different programming model from the prober's plain Python scheduler loop, and
  adopting it would be close to a rewrite, not a deploy target change.
- **Cold start:** a Lambda invocation after idle time starts a new execution environment,
  "loading the runtime, dependencies, and the function code," with a duration that "varies from
  under 100 milliseconds to over 1 second, depending on runtime, package size, and
  initialization logic"
  ([AWS Fargate or AWS Lambda?](https://docs.aws.amazon.com/decision-guides/latest/fargate-or-lambda/fargate-or-lambda.html)).
  This does not, by itself, rule out Lambda for a job that tolerates a short delay; it is the
  state loss on every one of those fresh starts that does.
- **Cost model, in general terms:** Lambda charges per invocation and per execution duration,
  scaled by the memory allocated to the function
  ([AWS Fargate or AWS Lambda?](https://docs.aws.amazon.com/decision-guides/latest/fargate-or-lambda/fargate-or-lambda.html)).
  A scheduler that wakes every few minutes, all day, every day, is closer to "always running"
  than to the short, bursty invocations this model is built for.
- **Networking:** by default a Lambda function reaches the public internet; attaching it to a
  VPC narrows it to only what that VPC can reach, and giving it internet access from there
  still needs the VPC itself to have a path out
  ([Giving Lambda Event Functions access to resources in an Amazon VPC](https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc.html)).
  An AWS account can have subnets with no internet path at all.
  The AWS stack's answer there is an optional S3 gateway VPC endpoint, and the spec is explicit
  that this is not a full answer even for the VM: "The endpoint makes the VM reach S3 only. A
  VM with no internet path still cannot reach the EC2 and STS APIs, the SSM service, the
  package mirrors, and the Spot Advisor feed" (spec 0004, section 4.4). A Lambda function
  placed in the same subnet would need the same set of VPC endpoints for the same AWS APIs
  (EC2, for example, for the Capacity Block calls of spec 0008). Moving to Lambda does not
  remove this constraint; it inherits it unchanged.

**Azure Functions.**

- **Maximum run time:** on the legacy Consumption plan, the default function timeout is 5
  minutes and the maximum is 10 minutes; the Flex Consumption, Premium, and Dedicated plans
  have no enforced maximum, only a grace period during scale-in or a platform update
  ([Azure Functions hosting options: "Function app timeout duration"](https://learn.microsoft.com/en-us/azure/azure-functions/functions-scale)).
  The sweeper's own spec cites this same Consumption limit for its own timer trigger: "On the
  Consumption plan, the default is 5 minutes and the maximum is 10 minutes" (spec 0007,
  section 3). The Azure prober has no queued search (requirements 1 and 2 are AWS only), so
  the cap that matters on Azure is that of 1 lane call: it runs the entries of its Region in
  series, each under its own deadline (requirement 3). For a real (not simulated) entry with
  `n: 2` and `n_max: 16`, as in the
  repo config ([`config/targets.yaml`](../config/targets.yaml)), 1 entry's deadline is
  4 x 150 s = 600 s, the whole 10 minutes, so a lane call of 2 or more such entries can pass
  the Consumption cap. Flex Consumption removes the cap and stays pay-per-use, billed "based
  on number of executions, the memory of instances when they're actively executing functions, plus the
  cost of any always ready instances"
  ([Azure Functions hosting options: "Billing"](https://learn.microsoft.com/en-us/azure/azure-functions/functions-scale)):
  the same page recommends Flex Consumption over the legacy Consumption plan for "new
  serverless function apps". Removing the run-time cap is not, by itself, what rules Azure
  Functions out here; the next bullet is.
- **State between invocations:** The Azure Functions guidance does not promise that an instance, or
  the state in its memory, survives from 1 invocation to the next. Microsoft's own performance
  guidance says "Functions should be stateless and idempotent if possible. Associate any
  required state information with your data"
  ([Improve Azure Functions performance and reliability](https://learn.microsoft.com/en-us/azure/azure-functions/performance-reliability)).
  On the legacy Consumption plan, "[a]pps can scale to zero when idle"
  ([Azure Functions hosting options: "Cold start behavior"](https://learn.microsoft.com/en-us/azure/azure-functions/functions-scale)),
  which ends whatever an instance held in memory. The Azure prober has no call queue, no token
  bucket, and no search whose state travels across queued calls (requirements 1 and 2 are AWS
  only); its halving and upward searches run to their end inside 1 lane call. What it holds in
  memory is requirement 3: 1 long-lived loop with its cadences, the Region lanes with their lock
  per Region, and the lock per (Region, SKU) that each entry holds. That is process state that
  this guidance asks an author, where possible, to keep with the data and not in the function.
- **Cold start:** "Apps can scale to zero when idle, meaning some requests might have more
  latencies at startup," on the legacy Consumption plan; Flex Consumption supports "always
  ready instances" and the Premium plan keeps at least 1 instance warm, to avoid this
  ([Azure Functions hosting options: "Cold start behavior"](https://learn.microsoft.com/en-us/azure/azure-functions/functions-scale)).
- **Cost model, in general terms:** the legacy Consumption plan bills "based on number of
  executions, execution time, and memory used"; the Premium plan bills "based on the number
  of core seconds and memory used across needed and prewarmed instances," and "[a]t least one
  instance per plan must always be kept warm," a standing cost
  ([Azure Functions hosting options: "Billing"](https://learn.microsoft.com/en-us/azure/azure-functions/functions-scale)).
- **Networking:** the Azure VM needs outbound access that the network owner sets up before the
  deploy, "for example through a NAT gateway or a firewall" (spec 0005, section 3.2); an
  egress proxy is optional on top of that (`egress_proxy_url`, spec 0005, section 3.4), and
  when it is set, the VM's own units get a proxy drop-in so `curl`, `pip`, and the Azure
  Resource Manager API all go through it (spec 0005, section 3.4, rules 1 to 5). An Azure
  Functions app would need the same kind of outbound path, and on the legacy Consumption plan
  it cannot get a controlled outbound path: that plan has no virtual network integration
  ([Azure Functions hosting options: "Networking features"](https://learn.microsoft.com/en-us/azure/azure-functions/functions-scale)
  lists "Virtual network integration (outbound)" as unsupported on the legacy Consumption
  plan). Flex Consumption does support it, so this gap is a Consumption-plan-specific
  constraint, not a reason to rule out Azure Functions generally; the state argument above is.

**A container service (AWS ECS/Fargate, Azure Container Apps).**

- **Maximum run time:** AWS states plainly that Fargate's execution time limit is "No hard
  limit," against Lambda's 15 minutes
  ([AWS Fargate or AWS Lambda?](https://docs.aws.amazon.com/decision-guides/latest/fargate-or-lambda/fargate-or-lambda.html)).
  Azure Container Apps sets no run-time cap either: its scaling docs say "[i]f you want to
  ensure that an instance of your revision is always running, set the minimum number of
  replicas to 1 or higher"
  ([Scaling in Azure Container Apps: "Scale definition"](https://learn.microsoft.com/en-us/azure/container-apps/scale-app)).
  Both container options can run the prober's own long-lived scheduler loop unmodified, the
  same as a VM.
- **State between invocations:** AWS states that Fargate "[c]ontainers can maintain in-memory
  state across requests while running"
  ([AWS Fargate or AWS Lambda?](https://docs.aws.amazon.com/decision-guides/latest/fargate-or-lambda/fargate-or-lambda.html)),
  which is what the AWS queue, bucket, and Capacity Block search in progress need, and what the
  scheduler loop with its Region lanes needs on both clouds. A container that
  keeps running holds its state for as long as it runs, as the VM's own process does. An Azure
  Container Apps replica is not a fixed process: "during platform upgrades or maintenance, you
  might temporarily see more replicas than expected"
  ([Scaling in Azure Container Apps: "Scale definition"](https://learn.microsoft.com/en-us/azure/container-apps/scale-app)),
  so the page does not promise one fixed replica for the whole run.
- **Cold start:** "Fargate is not generally impacted by cold starts in the same way as
  Lambda... After a task starts on Fargate, it is a long-running process"
  ([AWS Fargate or AWS Lambda?](https://docs.aws.amazon.com/decision-guides/latest/fargate-or-lambda/fargate-or-lambda.html)).
  Azure Container Apps scales to zero only by its own scale rules, with a default minimum of
  0 replicas unless the app sets a minimum of 1 or more
  ([Scaling in Azure Container Apps: "Scale definition"](https://learn.microsoft.com/en-us/azure/container-apps/scale-app)
  gives a default of 0 and states "[y]ou aren't billed usage charges if your container app
  scales to zero"); a minimum of 1 or more keeps 1 replica running continuously, the same as
  the Fargate case above.
- **Cost model, in general terms:** Fargate bills "per second, with a one-minute minimum
  charge," for the vCPU and memory a task is allocated, "regardless of whether the application
  is actively processing requests"
  ([AWS Fargate or AWS Lambda?](https://docs.aws.amazon.com/decision-guides/latest/fargate-or-lambda/fargate-or-lambda.html)).
  This is the same standing-cost shape as a VM that runs all the time: a container service
  does not turn the prober into a pay-per-use workload, because the prober's own scheduler
  never stops running.
- **Networking:** a Fargate task runs inside the same VPC and subnet the VM would use today,
  so the same S3 gateway endpoint question (spec 0004, section 4.4) and the same "EC2 and STS
  APIs, the SSM service, the package mirrors, and the Spot Advisor feed" gap apply unchanged.
  Azure Container Apps uses an Azure-managed network by default, reachable only to
  internet-accessible endpoints; an existing virtual network is optional, and is needed for
  features such as control of outbound traffic
  ([Networking in an Azure Container Apps Environment: "Virtual network type"](https://learn.microsoft.com/en-us/azure/container-apps/networking)).
  A route to the existing proxy would need that virtual network, a second network surface next
  to the VM's.

### 3. The pick: option 1, keep the VM

The VM keeps all 4 of the things the prober needs from its runtime (section above) with no
extra design: 1 long-running process holds its state for as long as it runs, the same lifetime
the scheduler loop already assumes (spec 0003, sections 6, 6A, and 6B). On AWS, that state is
the queue, the bucket, and a Capacity Block search's progress; on both clouds, it is the loop
and its Region lanes with their locks. The 1 cost a VM carries is the restart. On AWS, ADR 0012
already bounds the queue's loss to "the rest of 1 cycle" per restart, and explicitly declines
to spend effort removing that cost further, because the evidence does not call for it (ADR
0012, section 2, option 4; the repo rule is evidence before features). On both clouds, a
restart also costs the lane cycle in progress (requirement 4).

A FaaS runtime fails on requirements that differ by cloud. On AWS, Lambda fails on requirements
1 and 2: it keeps no state between invocations by design, which is exactly what the call queue,
the token bucket, and `aws_cb_longest`'s search need. On both clouds, a function also fails on
requirement 3: 1 long-lived loop with its Region lanes and their locks is not the stateless,
short-run shape of a function. On Azure, requirement 3 is the deciding one, because the Azure
prober has no queue. The run-time caps do not decide on either cloud: on AWS, 1
`aws_cb_longest` search alone fits in Lambda's 15 minutes, the 55-minute queue drain fits
in its 90-minute limit for some Lambda Managed Instances invocations, and an `aws_odcr` lane
call of 2 or more real entries can pass 15 minutes on standard Lambda (section 2); on Azure, Flex
Consumption has no cap (section 2).
Moving the VM's networking answer to a FaaS runtime removes nothing. On AWS, a Lambda function
in the same subnet has the same missing internet path and needs the same VPC endpoints. On
Azure, a function app needs the same outbound path or proxy, and on the legacy Consumption plan
it cannot get a controlled outbound path (section 2).

A container service (Fargate, Azure Container Apps with 1 or more replicas) keeps the state and
removes the run-time cap and, for a running container, keeps the state like a VM (a Container Apps replica has the caveat in section 2). It does not change the cost model much: Fargate bills for the
allocated resources while a task runs (section 2), and a Container Apps replica that stays
running may bill at a lower "idle" rate while it processes nothing
([Scaling in Azure Container Apps: "Scale definition"](https://learn.microsoft.com/en-us/azure/container-apps/scale-app)),
so the shape is a standing cost like a VM that never stops, because the prober's own scheduler
loop is designed to run continuously, not to scale to zero between wakes. A container service would trade the VM's own small, specific
surface (systemd units, a boot script, a release layout: ADR 0012) for a second kind of
infrastructure (a cluster or an environment, a task definition or a container app, an image
registry) that gives back nothing this ADR's own requirements ask for. The simpler option
that already does the job is the VM.

### 4. What would make this change

- **On AWS, the call queue, the token bucket, and a Capacity Block search's state move out of
  process memory**, for example to the data store the prober already writes to. ADR 0012
  rejected this for the restart question alone, calling it "a feature the evidence does not
  call for" against a bounded, already-accepted loss. A different reason to add it, for example a hot reload with
  no restart at all (the later change ADR 0012 already points to), could make a stateless
  runtime viable at the same time, because the AWS state that keeps a FaaS runtime out today
  would no longer live only in 1 process. On both clouds, the scheduler loop and its Region
  lanes with their locks would still need a home (requirement 3).
- **A probe's own run time shrinks well under a FaaS runtime's cap**, so that on AWS neither
  the cadence nor `aws_cb_longest`'s worst-case search needs a queue that spans a wake cycle,
  and on both clouds 1 Region lane call fits in 1 invocation.
  Splitting the search into 1 invocation per step, with its own externalized state, would
  itself be close to the hot-reload-style redesign above.
- **The scheduler itself is redesigned around a workflow engine with built-in state
  checkpointing**, for example AWS's own Lambda durable functions, which persist workflow
  state across invocations for up to 1 year. This is a different programming model from the
  prober's current scheduler loop, and adopting it is closer to a rewrite than a deploy target
  change, but it is the direction that would let the state this ADR's own evidence turns on
  live outside a single long-running process.

### 5. The sweepers are already scheduled functions, and that is correct

Each cloud has a sweeper that runs as a scheduled function, not on the prober VM: a
timer-triggered Azure Function App ([spec 0007](../spec/0007-sweeper-spec.md)) and, on AWS, a
Lambda function that EventBridge Scheduler starts every minute
([spec 0013](../spec/0013-aws-sweeper-spec.md), [ADR 0019](0019-the-aws-sweeper-is-a-scheduled-lambda-function.md)).
Both "must not... hold state between runs" (spec 0007, section 1; spec 0013, section 1). Each
makes its own listing call, decides, deletes or cancels, and exits, every minute, independent of
the prober VM. Neither has any of the 4 requirements above: no queue to carry between runs, no
multi-step search, and no cadence slower than its own 1-minute trigger that a restart could
interrupt mid-cycle. This is the shape FaaS fits: a short, stateless, single-purpose job with a
hard deadline already inside the platform's own limit (`functionTimeout: "00:05:00"`, spec 0007,
section 3; a 120 s function timeout, spec 0013, section 3). The AWS sweeper also runs outside
the VPC, so the subnet constraint above does not apply to it (ADR 0019). The sweepers and the
prober are not the same kind of job, and this ADR's choice for the prober does not reopen the
choice for the sweepers.

## Evidence

- Spec 0003, section 6 (the scheduler loop) and section 6A (the call queue, the token bucket,
  the restart effects, rule 8).
- Spec 0008, section 3B (`aws_cb_longest`), especially section 3B.2 (state travels in
  `Call.args`) and section 3B.5 (the plan and the call limit).
- [ADR 0012](0012-update-the-code-and-config-with-a-vm-side-pull-and-restart.md), section 2
  (the pick and the rejected options) and the restart-effects table.
- Spec 0007, section 1 (the Azure sweeper's own scope and no-state rule) and section 3 (the timer
  trigger and `functionTimeout`).
- Spec 0013, sections 1 and 3 (the AWS sweeper's no-state rule, schedule, and function timeout),
  and [ADR 0019](0019-the-aws-sweeper-is-a-scheduled-lambda-function.md).
- Spec 0003, section 6B, and [ADR 0020](0020-provisioning-probes-run-in-region-lanes-off-the-signal-thread.md)
  (the Region lanes).
- [ADR 0009](0009-allocation-probes-are-all-or-nothing-with-halving.md), section 2, and
  [ADR 0023](0023-the-provisioning-search-goes-up-after-a-full-success.md) (the halving and
  upward searches of the provisioning probes).
- Code: [`scheduler.py`](../prober/ftg_prober/scheduler.py) (`QUEUE_DEFAULTS`, `work`,
  `region_locks`, `start_lanes`), [`azure/prechecks.py`](../prober/ftg_prober/azure/prechecks.py)
  (`series_lock`, the entry budget), [`aws/odcr.py`](../prober/ftg_prober/aws/odcr.py) (the
  entry deadline), and the `plan` methods of `aws/cb_offerings.py`, `aws/cb_earliest.py`, and
  `aws/cb_longest.py`.
- Spec 0004, section 4.4 (the optional S3 gateway VPC endpoint, and what it does not cover).
- Spec 0005, section 3.2 (the network owner provides outbound access) and section 3.4 (the
  optional egress proxy for the Azure VM).
- [AWS Lambda quotas: function timeout (900 seconds, or 5,400 seconds for some Managed Instances
  invocations) and "Lambda is designed for short-lived
  compute tasks that do not retain or rely upon state between invocations"](https://docs.aws.amazon.com/lambda/latest/dg/gettingstarted-limits.html)
- [AWS Fargate or AWS Lambda? (execution time limit, state management, cold start, pricing
  model comparison table and discussion)](https://docs.aws.amazon.com/decision-guides/latest/fargate-or-lambda/fargate-or-lambda.html)
- [Giving Lambda functions access to resources in a VPC: internet access when attached to a
  VPC](https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc.html)
- [Azure Functions hosting options: function app timeout duration, cold start behavior,
  billing, and networking features by plan](https://learn.microsoft.com/en-us/azure/azure-functions/functions-scale)
- [Improve Azure Functions performance and reliability: "Write functions to be
  stateless"](https://learn.microsoft.com/en-us/azure/azure-functions/performance-reliability)
- [Networking in an Azure Container Apps Environment: virtual network type](https://learn.microsoft.com/en-us/azure/container-apps/networking)
- [Scaling in Azure Container Apps: minimum replicas and billing while scaled to
  zero](https://learn.microsoft.com/en-us/azure/container-apps/scale-app)

## Consequences

- No code or spec changes. This ADR records a choice already in effect; it does not change
  the prober, the sweeper, or either Terraform stack.
- A later proposal to move the prober off a VM must first show how its state survives a FaaS
  runtime's invocation boundary, not only how a call fits inside a run-time cap: on AWS, the
  call queue, the token bucket, and a Capacity Block search's state; on both clouds, the
  scheduler's cadences and the Region lanes with their locks.
- The sweepers stay scheduled functions (the Azure Function App and the AWS Lambda function); this ADR does not ask for a review of that choice.
