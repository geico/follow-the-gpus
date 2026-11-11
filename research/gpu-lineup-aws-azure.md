---
title: "GPU lineup on AWS and Azure"
date: 2026-10-11
---

# GPU lineup on AWS and Azure

## Summary

This note lists the GPU models that AWS and Azure document, and the instance families or VM series that carry each model. All pages were read on 2026-10-11.

- **AWS** documents 13 NVIDIA and AMD GPU models in current-generation families, from NVIDIA B300 to AMD Radeon Pro V520. The AWS table also labels the Habana Gaudi HL-205 (DL1) a GPU. This note counts Gaudi as an other accelerator (see [Scope](#scope)). AWS lists G3 (M60) and P3 and P3dn (V100) as previous generation. ([AWS accelerated specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html), [AWS previous generation specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/pg.html))
- **AWS Spot.** The AWS family summary table gives Spot support "Yes" for all GPU families except P6e-GB200. ([AWS accelerated specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html))
- **Azure** documents GPU sizes in 4 families: NC, ND, NG, and NV. ([Azure VM sizes overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview))
- **Azure** has 21 GPU series pages in its current sizes documentation. Archived pages for older retired series, for example NC (K80) and NCv2 (P100), are under previous-versions. ([NCv2 archived page](https://learn.microsoft.com/en-us/previous-versions/azure/virtual-machines/sizes/gpu-accelerated/ncv2-series))
- **Azure retirements.** Five of the 21 series are retired: ND (v1), NV (v1), NCv3, NVv3, and NVv4. The other 16 pages show no retirement notice. ([Azure retirements](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/lifecycle/retirements-and-capacity-restrictions), series pages in the [Azure](#azure) tables)
- **Azure Spot.** The Azure GPU series pages do not state Spot support. The Azure Spot page excludes only B-series and promo sizes. ([Azure Spot VMs](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms))
- **Shared models.** Both clouds document NVIDIA B300, GB200 systems, H200, H100, A100, V100, and T4.
- **Fractional GPUs.** Both clouds sell sizes with a fraction of 1 GPU: AWS G6f and Gr6f, and Azure NVads A10 v5, NVads V710 v5, NGads V620, and NC RTX PRO 6000 BSE v6.

For Spot placement scores per AWS GPU type, see [AWS Spot placement score per GPU type](aws-spot-placement-score-per-gpu-type.md).

## Scope

This note covers GPUs only. AWS also documents other accelerators in the same category: Gaudi (DL1), Qualcomm AI100 (DL2q), Inferentia (Inf1, Inf2), Trainium (Trn1, Trn1n, Trn2, Trn2u), FPGA (F2), and media accelerators (VT1). ([AWS accelerated specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)) This note does not cover them.

Retired and previous-generation series are out of scope for probing. This note lists them only in short lists.

Memory units follow each source. AWS gives GiB. Azure spec tables give GB, except the ND GB200 v6 host specifications table, which gives GiB.

## AWS

### Current generation

Source for every row: the "Performance specifications" and "Instance family summary" tables on the [AWS accelerated specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) page. "GPUs" is the number of GPUs per instance.

| GPU (AWS name) | Family | Instance types: GPUs | Memory per GPU | Spot | Source |
|---|---|---|---|---|---|
| NVIDIA B300 | P6-B300 | p6-b300.48xlarge: 8 | 268 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA B200 | P6-B200 | p6-b200.48xlarge: 8 | 179 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA B200, with NVIDIA Grace CPU (arm64) | P6e-GB200 | p6e-gb200.36xlarge: 4 | 185 GiB | No | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA H200 | P5en | p5en.48xlarge: 8 | 141 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA H200 | P5e | p5e.48xlarge: 8 | 141 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA H100 | P5 | p5.4xlarge: 1, p5.48xlarge: 8 | 80 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA A100 | P4de | p4de.24xlarge: 8 | 80 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA A100 | P4d | p4d.24xlarge: 8 | 40 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA RTX PRO Server 6000 | G7e | g7e.2xlarge, 4xlarge, 8xlarge: 1, 12xlarge: 2, 24xlarge: 4, 48xlarge: 8 | 96 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA RTX PRO 4500 | G7 | g7.2xlarge, 4xlarge, 8xlarge: 1, 12xlarge: 2, 24xlarge: 4, 48xlarge: 8 | 32 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA L40S | G6e | g6e.xlarge, 2xlarge, 4xlarge, 8xlarge, 16xlarge: 1, 12xlarge, 24xlarge: 4, 48xlarge: 8 | 44 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA L4 | G6 | g6.xlarge, 2xlarge, 4xlarge, 8xlarge, 16xlarge: 1, 12xlarge, 24xlarge: 4, 48xlarge: 8 | 22 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA L4 | Gr6 | gr6.4xlarge, gr6.8xlarge: 1 | 22 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA L4 (fraction) | G6f | g6f.large, xlarge: 0.125, 2xlarge: 0.25, 4xlarge: 0.5 | 2.79, 5.59, and 11.18 GiB per instance | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA L4 (fraction) | Gr6f | gr6f.4xlarge: 0.5 | 11.18 GiB per instance | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA A10G | G5 | g5.xlarge, 2xlarge, 4xlarge, 8xlarge, 16xlarge: 1, 12xlarge, 24xlarge: 4, 48xlarge: 8 | 22 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA T4g, with Graviton2 CPU (arm64) | G5g | g5g.xlarge, 2xlarge, 4xlarge, 8xlarge: 1, 16xlarge, metal: 2 | 16 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| NVIDIA T4 | G4dn | g4dn.xlarge, 2xlarge, 4xlarge, 8xlarge, 16xlarge: 1, 12xlarge: 4, metal: 8 | 16 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |
| AMD Radeon Pro V520 | G4ad | g4ad.xlarge, 2xlarge, 4xlarge: 1, 8xlarge: 2, 16xlarge: 4 | 8 GiB | Yes | [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html) |

Notes:

- The AWS spec table names the P6e-GB200 accelerator "4 x NVIDIA B200 GPU". The P6 product page says that P6e UltraServers use NVIDIA GB200 NVL72. ([AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html), [AWS P6 page](https://aws.amazon.com/ec2/instance-types/p6/))
- The P6 product page says that P6e-GB200 instances "are only available in UltraServers". It lists 2 UltraServer types: u-p6e-gb200x72 (72 GPUs) and u-p6e-gb200x36 (36 GPUs). ([AWS P6 page](https://aws.amazon.com/ec2/instance-types/p6/))
- AWS announced general availability of P6e-GB300 UltraServers on 2025-12-02. The announcement tells customers to contact AWS sales. The AWS spec table does not list a P6e-GB300 instance type. ([AWS announcement](https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-ec2-p6e-gb300-ultraservers-nvidia-gb300-nvl72-generally-available/), [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html))
- The P6 product page gives p6-b300.48xlarge GPU memory as "2,144" GB HBM3e. The spec table gives 2148 GiB (8 x 268 GiB). But 8 x 268 is 2,144, so the spec table total does not match its own per-GPU value. ([AWS P6 page](https://aws.amazon.com/ec2/instance-types/p6/), [AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html))

### Previous generation (out of scope for probing)

The AWS accelerated page points to a separate page for previous generation types, "such as G3 instances". ([AWS accelerated specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)) That page lists these GPU families ([AWS previous generation](https://docs.aws.amazon.com/ec2/latest/instancetypes/pg.html)):

- P3dn: NVIDIA V100
- P3: NVIDIA V100
- G3: NVIDIA M60

## Azure

### Series with no retirement notice

Each row comes from the series page: the "Host specifications" table, the "Accelerators" tab, and the summary text. "GPUs" is the number of GPUs per VM. A fraction means a part of 1 GPU.

| GPU (Azure name) | Series | Sizes: GPUs | Memory per GPU | Source |
|---|---|---|---|---|
| NVIDIA Blackwell Ultra (summary: "Blackwell B300"), with NVIDIA Grace CPU | ND GB300 v6 | Standard_ND128isr_GB300_v6: 4, Standard_ND128isrG5_GB300_v6: 4 | 288 GB | [ND GB300 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb300-v6-series) |
| NVIDIA Blackwell (summary: GB200), with NVIDIA Grace CPU | ND GB200 v6 | Standard_ND128isr_NDR_GB200_v6: 4 | 192 GiB | [ND GB200 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series) |
| NVIDIA H200 | ND H200 v5 | Standard_ND96isr_H200_v5: 8 | 141 GB | [ND H200 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-h200-v5-series) |
| NVIDIA H100 | ND H100 v5 | Standard_ND96isr_H100_v5: 8 | 80 GB | [ND H100 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndh100v5-series) |
| AMD Instinct MI300X | ND MI300X v5 | Standard_ND96isr_MI300X_v5: 8 | 192 GB | [ND MI300X v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndmi300xv5-series) |
| NVIDIA A100 80 GB | NDm A100 v4 | Standard_ND96amsr_A100_v4: 8 | 80 GB | [NDm A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndma100v4-series) |
| NVIDIA A100 40 GB | ND A100 v4 | Standard_ND96asr_v4: 8 | 40 GB | [ND A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndasra100v4-series) |
| NVIDIA Tesla V100 | NDv2 | Standard_ND40rs_v2: 8 | 32 GB | [NDv2](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndv2-series) |
| NVIDIA RTX PRO 6000 Blackwell Server Edition | NC RTX PRO 6000 BSE v6 | Standard_NC36ds_xl_RTXPRO6000BSE_v6: 1/4, Standard_NC72ds_xl_RTXPRO6000BSE_v6: 1/2, Standard_NC144ds_xl_RTXPRO6000BSE_v6: 1, Standard_NC288ds_xl_RTXPRO6000BSE_v6: 2. Compute optimized: Standard_NC24lds_xl_RTXPRO6000BSE_v6, Standard_NC36lds_xl_RTXPRO6000BSE_v6: 1/4, Standard_NC72lds_xl_RTXPRO6000BSE_v6: 1/2, Standard_NC144lds_xl_RTXPRO6000BSE_v6: 1, Standard_NC288lds_xl_RTXPRO6000BSE_v6: 2 | 96 GB | [NC RTX PRO 6000 BSE v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series) |
| NVIDIA H100 NVL | NCads H100 v5 | Standard_NC40ads_H100_v5: 1, Standard_NC80adis_H100_v5: 2 | 94 GB | [NCads H100 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncadsh100v5-series) |
| NVIDIA H100 NVL, confidential VM | NCCads H100 v5 | Standard_NCC40ads_H100_v5: 1 | 94 GB | [NCCads H100 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nccadsh100v5-series) |
| NVIDIA A100 PCIe | NC A100 v4 | Standard_NC24ads_A100_v4: 1, Standard_NC48ads_A100_v4: 2, Standard_NC96ads_A100_v4: 4 | 80 GB | [NC A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nca100v4-series) |
| NVIDIA Tesla T4 | NCasT4_v3 | Standard_NC4as_T4_v3, Standard_NC8as_T4_v3, Standard_NC16as_T4_v3: 1, Standard_NC64as_T4_v3: 4 | 16 GB | [NCasT4_v3](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncast4v3-series) |
| NVIDIA A10 | NVads A10 v5 | Standard_NV6ads_A10_v5: 1/6, Standard_NV12ads_A10_v5: 1/3, Standard_NV18ads_A10_v5: 1/2, Standard_NV36ads_A10_v5: 1, Standard_NV36adms_A10_v5: 1, Standard_NV72ads_A10_v5: 2 | 24 GB | [NVads A10 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsa10v5-series) |
| AMD Radeon Pro V710 | NVads V710 v5 | Standard_NV4ads_V710_v5: 1/6, Standard_NV8ads_V710_v5: 1/3, Standard_NV12ads_V710_v5: 1/2, Standard_NV24ads_V710_v5: 1, Standard_NV28adms_V710_v5: 1 | 24 GB (from the Accelerators tab) | [NVads V710 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsv710-v5-series) |
| AMD Radeon PRO V620 | NGads V620 | Standard_NG8ads_V620_v1: 1/4, Standard_NG16ads_V620_v1: 1/2, Standard_NG32ads_V620_v1: 1, Standard_NG32adms_V620_v1: 1 | 32 GB | [NGads V620](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ngadsv620-series) |

Notes:

- The ND GB300 v6 summary says each VM has "four NVIDIA Blackwell B300 GPUs". It gives "288 GB HBM3E per GPU vs. 192 GB" for GB200. ([ND GB300 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb300-v6-series))
- The ND GB200 v6 summary says each VM has "two NVIDIA Grace CPUs and four NVIDIA Blackwell GPUs". It says that 18 VMs form 1 GB200 NVL72 rack of 72 GPUs. ([ND GB200 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series))
- On the ND GB300 v6, ND GB200 v6, and NDm A100 v4 pages, the Accelerators tab gives 1 GPU's memory (288, 192, and 80) for the whole VM. The Host specifications table gives the same numbers per GPU. This note uses the per-GPU values. ([ND GB300 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb300-v6-series), [ND GB200 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series), [NDm A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndma100v4-series))
- The NDv2 Accelerators tab leaves the GPU count empty. The Host specifications table gives 8, and the summary says "8 NVIDIA Tesla V100 NVLINK-connected GPUs, each with 32 GB". ([NDv2](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndv2-series))
- The NVads V710 v5 Host specifications table does not give GPU memory. The value comes from the Accelerators tab. ([NVads V710 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsv710-v5-series))
- The NC RTX PRO 6000 BSE v6 overview names West US 2 as a Region where the series is generally available (GA). ([NC RTX PRO 6000 BSE v6 overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series-overview))
- The NCCads H100 v5 summary says its Trusted Execution Environment "spans confidential VM on the CPU and attached GPU". ([NCCads H100 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nccadsh100v5-series))
- The NC A100 v4 page labels NCads H100 v5 the "Current" NC series: "We are currently only deploying net new capacity for the Current NC series, the NCads_H100_v5-series." ([NC A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nca100v4-series))
- The Azure lifecycle overview tells readers to check the family page for the lifecycle stage of each series. ([Azure lifecycle overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/lifecycle/lifecycle-overview))
- The GPU family pages do not label each series with a stage. Each family page links to the End of Life list, which names NVv3 and NVv4. The NV family page also gives the NVv3 and NVv4 retirement date. ([NC family](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-family), [ND family](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-family), [NV family](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nv-family), [End of Life list](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/lifecycle/end-of-life-sizes-list))

### Retired series (out of scope for probing)

- NVv3: NVIDIA Tesla M60, retired 2026-09-30 ([NVv3](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvv3-series))
- NVv4: AMD Instinct MI25, retired 2026-09-30 ([NVv4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvv4-series))
- NCv3: NVIDIA Tesla V100, retired 2025-09-30 ([NCv3](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncv3-series), [Azure retirements](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/lifecycle/retirements-and-capacity-restrictions))
- ND (v1): NVIDIA Tesla P40, retired 2023-09-06 ([ND](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-series))
- NV (v1): NVIDIA Tesla M60, retired 2023-09-06 ([NV](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nv-series))

## Gaps

- **Azure Spot per series.** No Azure GPU series page states Spot support or a Spot exclusion. The general Spot page excludes only "B-series" and "Promo versions of any size". Spot eligibility of each GPU size is not verified here. ([Azure Spot VMs](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms))
- **AWS P6e-GB300.** No public AWS spec table gives the P6e-GB300 instance type, GPU count, or GPU memory. ([AWS specs](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html))
- **Regional availability.** Except for 1 note on NC RTX PRO 6000 BSE v6, this note does not cover Regional availability.

## Sources

- [Specifications for Amazon EC2 accelerated computing instances](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)
- [Specifications for Amazon EC2 previous generation instances](https://docs.aws.amazon.com/ec2/latest/instancetypes/pg.html)
- [Amazon EC2 P6e UltraServers and P6 instances](https://aws.amazon.com/ec2/instance-types/p6/)
- [Amazon EC2 P6e-GB300 UltraServers generally available (2025-12-02)](https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-ec2-p6e-gb300-ultraservers-nvidia-gb300-nvl72-generally-available/)
- [Azure VM sizes overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview)
- [Azure NC family](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-family)
- [Azure ND family](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-family)
- [Azure NV family](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nv-family)
- [Azure End of Life VM size series](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/lifecycle/end-of-life-sizes-list)
- [Azure NCv2 archived page (previous-versions)](https://learn.microsoft.com/en-us/previous-versions/azure/virtual-machines/sizes/gpu-accelerated/ncv2-series)
- Azure GPU series pages, linked in each table row
- [Azure NC RTX PRO 6000 BSE v6 overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series-overview)
- [Azure retired and retiring VM size series](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/lifecycle/retirements-and-capacity-restrictions)
- [Azure VM lifecycle overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/lifecycle/lifecycle-overview)
- [Azure Spot Virtual Machines](https://learn.microsoft.com/en-us/azure/virtual-machines/spot-vms)
