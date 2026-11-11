---
title: Project description
date: 2026-10-11
---

# Project description

## The problem

A cloud region can refuse an On-Demand request. The most sought-after GPUs are in high demand, and they can be unavailable at the time of the request. On some clouds, you can reserve these GPUs ahead of time with a commitment for days, months, or years. A commitment pays off only when the workload justifies it. Many workloads do not justify a large spend just to find out whether they are worth a large spend. These workloads need GPUs without a commitment. Today, their requests fail.

## The broker

The broker increases the chance that a GPU request succeeds. A hybrid cloud fabric can span many regions and more than 1 public cloud, for example AWS and Azure. When a user asks for a GPU, the broker searches all of these regions at the same time. It starts a node where capacity exists, and it runs the workload as fast as possible. The user needs a GPU, not a GPU in a specific place. Think of it as flying standby to any destination. The broker improves the chance of success. It does not guarantee availability.

This design assumes that any region is acceptable. The project accepts this assumption for now.

## Design limits

The architecture must not depend on self-managed or provider-managed Kubernetes. It must not depend on a specific serving gateway. These parts must be replaceable.

## Sources

- [EC2 launch troubleshooting](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/troubleshooting-launch.html)
- [Azure allocation failure](https://learn.microsoft.com/en-us/troubleshoot/azure/virtual-machines/windows/allocation-failure)
- [Google Cloud consumption models](https://docs.cloud.google.com/ai-hypercomputer/docs/consumption-models)
- [Microsoft FY26 Q4 earnings](https://www.microsoft.com/en-us/investor/events/fy-2026/earnings-fy-2026-q4)
- [AWS News Blog, 2023-10-31: EC2 Capacity Blocks for ML](https://aws.amazon.com/blogs/aws/announcing-amazon-ec2-capacity-blocks-for-ml-to-reserve-gpu-capacity-for-your-machine-learning-workloads/)
- [AWS Capacity Blocks](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html)
- [Google Cloud reserve capacity](https://docs.cloud.google.com/ai-hypercomputer/docs/reserve-capacity)
- [Azure capacity reservation](https://learn.microsoft.com/en-us/azure/virtual-machines/capacity-reservation-overview)
- [EC2 Reserved Instances](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-reserved-instances.html)
- [AWS regions and zones](https://aws.amazon.com/about-aws/global-infrastructure/regions_az/)
- [Azure regions overview](https://learn.microsoft.com/en-us/azure/reliability/regions-overview)
