---
title: "AWS and Azure GPU equivalents"
date: 2026-10-11
---

# AWS and Azure GPU equivalents

## Summary

This note maps each current AWS GPU instance type to the nearest Azure GPU VM size. The keys are the GPU model, the GPU count per node, and the interconnect. All specs come from the AWS instance type pages and the Microsoft Learn size pages, read on 2026-10-11.

- 9 node shapes have the same GPU model and the same GPU count on both clouds. They are 8x H100, 8x H200, 8x A100 (40 GB and 80 GB), 4x GB200, 1x and 2x RTX PRO 6000, and 1x and 4x T4.
- AWS has 6 GPU models that no Azure size lists: L40S, L4, RTX PRO 4500, A10G, T4G, and Radeon Pro V520.
- AWS has 8-GPU B200 and B300 nodes with an x86 host. Azure has B200-class and B300-class GPUs only in 4-GPU VMs with Grace CPUs.
- Azure has 6 GPU models that no AWS instance type lists: MI300X, H100 NVL, A100 PCIe, A10, Radeon PRO V620, and Radeon Pro V710.
- The 8-GPU training nodes use different scale-out networks. AWS uses Elastic Fabric Adapter (EFA). Azure uses InfiniBand.

This note is a mapping only. It does not list the full lineup of each cloud, and it does not compare prices or capacity.

## How to read the Match column

- **Same:** The two sources name the same GPU model, and the GPU count per node is the same.
- **Nearest (judgment):** No size on the other cloud has the same GPU model and count. The size in the row is the closest by stated specs: GPU model name, GPU memory, GPU count, and CPU type. This is a judgment of this note, not a vendor statement.
- **None:** The other cloud has no GPU size with a close stated spec.

## Mapping by GPU model and GPU count

GPU counts and GPU memory are as each source states them. AWS gives GPU memory in GiB on its spec page. Azure gives GPU memory in GB or GiB per page.

### Data center GPUs with HBM

| GPU | GPUs per node | AWS instance type | Azure VM size | Match |
|---|---|---|---|---|
| H100, 80 GB | 8 | `p5.48xlarge` ([AWS spec](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)) | `Standard_ND96isr_H100_v5` ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndh100v5-series)) | Same |
| H200, 141 GiB / 141 GB | 8 | `p5e.48xlarge`, `p5en.48xlarge` ([AWS spec](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)) | `Standard_ND96isr_H200_v5` ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-h200-v5-series)) | Same |
| A100, 40 GB | 8 | `p4d.24xlarge` ([AWS P4](https://aws.amazon.com/ec2/instance-types/p4/)) | `Standard_ND96asr_v4` ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndasra100v4-series)) | Same |
| A100, 80 GB | 8 | `p4de.24xlarge` ([AWS P4](https://aws.amazon.com/ec2/instance-types/p4/)) | `Standard_ND96amsr_A100_v4` ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndma100v4-series)) | Same |
| Blackwell with Grace CPU (GB200) | 4 | `p6e-gb200.36xlarge` ([AWS spec](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)) | `Standard_ND128isr_NDR_GB200_v6` ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series)) | Same |
| Blackwell Ultra with Grace CPU (GB300) | 4 on Azure, not stated on AWS | P6e-GB300 UltraServer, no instance type on the spec page ([AWS P6](https://aws.amazon.com/ec2/instance-types/p6/)) | `Standard_ND128isr_GB300_v6`, `Standard_ND128isrG5_GB300_v6` ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb300-v6-series)) | Nearest (judgment) |
| B300, x86 host | 8 | `p6-b300.48xlarge` ([AWS spec](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)) | No 8-GPU size. Nearest is ND GB300 v6, 4 GPUs with Grace CPUs. | Nearest (judgment) |
| B200, x86 host | 8 | `p6-b200.48xlarge` ([AWS spec](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)) | No 8-GPU size. Nearest is ND GB200 v6, 4 GPUs with Grace CPUs. | Nearest (judgment) |
| H100, 1 GPU | 1 | `p5.4xlarge`, H100 80 GB ([AWS P5](https://aws.amazon.com/ec2/instance-types/p5/)) | `Standard_NC40ads_H100_v5`, H100 NVL 94 GB, PCIe ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncadsh100v5-series)) | Nearest (judgment) |

Notes on these rows:

- **GB200.** The AWS spec page lists `p6e-gb200.36xlarge` with "4 x NVIDIA B200 GPU" and an NVIDIA Grace (arm64) CPU. The AWS P6 page says that P6e UltraServers use NVIDIA GB200 NVL72. P6e-GB200 instances are "only available in UltraServers". ([AWS spec](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html), [AWS P6](https://aws.amazon.com/ec2/instance-types/p6/)) Azure says that each ND GB200 v6 VM has 2 Grace CPUs and 4 Blackwell GPUs, in groups of 18 VMs per rack. ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series))
- **GB300.** AWS announced P6e-GB300 UltraServers as generally available on 2025-12-02. The announcement names no instance size, and it says to contact AWS sales. The AWS spec page lists no P6e-GB300 instance type. ([AWS announcement](https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-ec2-p6e-gb300-ultraservers-nvidia-gb300-nvl72-generally-available/), [AWS spec](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)) Thus the GPU count per AWS node is not stated. Both clouds name the NVIDIA GB300 NVL72 system. ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb300-v6-series))
- **B200 and B300 in 8-GPU nodes.** AWS uses an Intel x86 host for P6-B200 and P6-B300. Azure has no x86 size with B200 or B300 GPUs. ([AWS spec](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html), [Azure sizes overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview))
- **1x H100.** The two GPUs are different H100 variants. AWS states H100 with 80 GB HBM3. Azure states H100 NVL with 94 GB, and calls it a PCIe H100. ([AWS P5](https://aws.amazon.com/ec2/instance-types/p5/), [Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncadsh100v5-series)) Azure also has `Standard_NCC40ads_H100_v5`, a confidential VM with 1 H100 GPU of 94 GB. ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nccadsh100v5-series))
- **GPU Direct RDMA on 1x H100.** AWS says that `p5.4xlarge` does not support GPUDirect RDMA. ([AWS P5](https://aws.amazon.com/ec2/instance-types/p5/))

### Professional and inference GPUs

| GPU | GPUs per node | AWS instance type | Azure VM size | Match |
|---|---|---|---|---|
| RTX PRO 6000 Blackwell Server Edition, 96 GB | 1 | `g7e.2xlarge`, `g7e.4xlarge`, `g7e.8xlarge` | `Standard_NC144ds_xl_RTXPRO6000BSE_v6`, `Standard_NC144lds_xl_RTXPRO6000BSE_v6` | Same |
| RTX PRO 6000 Blackwell Server Edition, 96 GB | 2 | `g7e.12xlarge` | `Standard_NC288ds_xl_RTXPRO6000BSE_v6`, `Standard_NC288lds_xl_RTXPRO6000BSE_v6` | Same |
| T4, 16 GB | 1 | `g4dn.xlarge` to `g4dn.16xlarge` (5 sizes) | `Standard_NC4as_T4_v3`, `Standard_NC8as_T4_v3`, `Standard_NC16as_T4_v3` | Same |
| T4, 16 GB | 4 | `g4dn.12xlarge` | `Standard_NC64as_T4_v3` | Same |
| A10G (AWS) and A10 (Azure) | 1 | `g5.xlarge` to `g5.16xlarge` (5 sizes) | `Standard_NV36ads_A10_v5`, `Standard_NV36adms_A10_v5` | Nearest (judgment) |
| T4G, arm64 host | 1 or 2 | `g5g.xlarge` to `g5g.metal` | No arm64 size with a T4-class GPU. Nearest is NCasT4_v3, T4 with x86 host. | Nearest (judgment) |

Sources for this table: [AWS spec](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html), [AWS G7e](https://aws.amazon.com/ec2/instance-types/g7e/), [Azure NC RTX PRO 6000 BSE v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series), [Azure NCasT4_v3](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncast4v3-series), [Azure NVadsA10 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsa10v5-series).

Notes on these rows:

- **RTX PRO 6000 status.** Microsoft announced the NC RTX PRO 6000 BSE v6 series (NCv6) as a public preview on 2025-11-24. This review found no general availability statement. ([Tech Community](https://techcommunity.microsoft.com/blog/azurehighperformancecomputingblog/azure-ncv6-public-preview-the-new-unified-platform-for-converged-ai-and-visual-c/4472704))
- **RTX PRO 6000 size groups.** Azure has 2 groups of sizes. The `ds_xl` sizes are "General Purpose" and the `lds_xl` sizes are "Compute Optimized". ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series))
- **A10G and A10.** The model names are different. AWS states 24 GB per A10G GPU on its product page. Azure states 24 GiB per A10 GPU. ([AWS G5](https://aws.amazon.com/ec2/instance-types/g5/), [Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsa10v5-series)) Each Azure NVadsA10 v5 VM includes a GRID license. ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsa10v5-series))
- **T4G.** AWS G5g uses AWS Graviton2 (arm64) CPUs and NVIDIA T4G GPUs. ([AWS G5g](https://aws.amazon.com/ec2/instance-types/g5g/))

## Interconnect of the matched pairs

"In node" is the GPU-to-GPU link inside 1 node. "Between nodes" is the scale-out network. The text gives only what the source states.

| Pair | AWS in node | AWS between nodes | Azure in node | Azure between nodes |
|---|---|---|---|---|
| 8x H100 | "900 GB/s NVSwitch" ([AWS P5](https://aws.amazon.com/ec2/instance-types/p5/)) | 3200 Gbps EFA ([AWS P5](https://aws.amazon.com/ec2/instance-types/p5/)) | "NVLINK 4.0" ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndh100v5-series)) | 400 Gbps Quantum-2 CX7 InfiniBand per GPU, 3.2 Tbps per VM ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndh100v5-series)) |
| 8x H200 | "900 GB/s NVSwitch" ([AWS P5](https://aws.amazon.com/ec2/instance-types/p5/)) | 3200 Gbps EFA ([AWS P5](https://aws.amazon.com/ec2/instance-types/p5/)) | "900 GB/s NVLink" ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-h200-v5-series)) | 400 Gbps Quantum-2 CX7 InfiniBand per GPU, 3.2 Tb/s per VM ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-h200-v5-series)) |
| 8x A100 40 GB | "600 GB/s NVSwitch" ([AWS P4](https://aws.amazon.com/ec2/instance-types/p4/)) | 400 Gbps, ENA and EFA ([AWS P4](https://aws.amazon.com/ec2/instance-types/p4/)) | "NVLINK 3.0" ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndasra100v4-series)) | HDR InfiniBand, 1 connection per GPU ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndasra100v4-series)) |
| 8x A100 80 GB | "600 GB/s NVSwitch" ([AWS P4](https://aws.amazon.com/ec2/instance-types/p4/)) | 400 Gbps, ENA and EFA ([AWS P4](https://aws.amazon.com/ec2/instance-types/p4/)) | "NVLINK 3.0" ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndma100v4-series)) | HDR InfiniBand, 1 connection per GPU ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndma100v4-series)) |
| 4x GB200 | NVLink domain of up to 72 GPUs ([AWS P6](https://aws.amazon.com/ec2/instance-types/p6/)) | 3200 Gbps EFA per instance ([AWS spec](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)) | Fifth-generation NVLink, 4x 1.8 TB/s per VM, 72 GPUs per rack ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series)) | 4 Quantum-2 CX7 InfiniBand connections per VM ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series)) |
| GB300 | NVIDIA GB300 NVL72 ([AWS announcement](https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-ec2-p6e-gb300-ultraservers-nvidia-gb300-nvl72-generally-available/)) | Not stated | Fifth-generation NVLink, 4x 1.8 TB/s per VM, 72 GPUs per rack ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb300-v6-series)) | 800 Gbps InfiniBand per GPU ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb300-v6-series)) |
| 1x H100 | Not applicable | 100 Gbps EFA, no GPUDirect RDMA ([AWS P5](https://aws.amazon.com/ec2/instance-types/p5/)) | Not applicable | No InfiniBand stated. 40,000 Mbps network ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncadsh100v5-series)) |
| RTX PRO 6000 | GPUDirect P2P via PCIe ([AWS G7e](https://aws.amazon.com/ec2/instance-types/g7e/)) | EFA from `g7e.8xlarge` up, up to 1600 Gbps ([AWS spec](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)) | Not stated | No InfiniBand stated. Up to 180,000 Mbps network ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series)) |
| T4 | Not stated | EFA on `g4dn.8xlarge` and larger ([AWS spec](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html)) | Not stated | No InfiniBand stated. Up to 32,000 Mbps network ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncast4v3-series)) |

Two more facts about scale-out:

- Azure sets up the InfiniBand connections automatically between VMs in the same virtual machine scale set. The ND H100 v5, ND H200 v5, and ND A100 v4 pages state this. ([ND H100 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndh100v5-series), [ND H200 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-h200-v5-series), [ND A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndasra100v4-series))
- AWS deploys P4d, P5, P5e, P5en, and P6 instances in EC2 UltraClusters. ([AWS P4](https://aws.amazon.com/ec2/instance-types/p4/), [AWS P5](https://aws.amazon.com/ec2/instance-types/p5/), [AWS P6](https://aws.amazon.com/ec2/instance-types/p6/))

## Sizes with no equivalent

### AWS sizes with no Azure equivalent

| GPU | AWS instance types | GPUs per node | Why no match |
|---|---|---|---|
| L40S | G6e | 1, 4, 8 | No Azure size lists the L40S. |
| L4 | G6, Gr6 | 1, 4, 8 | No Azure size lists the L4. |
| L4, fraction | G6f, Gr6f | 1/8, 1/4, 1/2 | No Azure size lists the L4. |
| RTX PRO 4500 Blackwell Server Edition | G7 | 1, 2, 4, 8 | No Azure size lists this GPU. |
| RTX PRO 6000 Blackwell Server Edition | `g7e.24xlarge`, `g7e.48xlarge` | 4, 8 | Azure RTX PRO 6000 sizes stop at 2 GPUs. |
| A10G | `g5.12xlarge`, `g5.24xlarge`, `g5.48xlarge` | 4, 8 | Azure A10 sizes stop at 2 GPUs. |
| T4 | `g4dn.metal` | 8 | Azure T4 sizes stop at 4 GPUs. |
| Radeon Pro V520 | G4ad | 1, 2, 4 | Azure has other AMD Radeon Pro GPUs (V620, V710), not the V520. |

Sources: [AWS spec](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html), [AWS G7](https://aws.amazon.com/ec2/instance-types/g7/), [Azure sizes overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview), and the Azure series pages in the Sources list.

### Azure sizes with no AWS equivalent

| GPU | Azure series | GPUs per node | Why no match |
|---|---|---|---|
| AMD Instinct MI300X, 192 GB | ND MI300X v5, `Standard_ND96isr_MI300X_v5` | 8 | The AWS spec page lists no AMD Instinct GPU. |
| H100 NVL, 94 GB | NCads H100 v5, `Standard_NC80adis_H100_v5` | 2 | AWS has no 2-GPU H100 type. |
| A100 PCIe, 80 GB | NC A100 v4 | 1, 2, 4 | AWS lists A100 only in 8-GPU types. |
| A10, fraction | NVadsA10 v5 | 1/6, 1/3, 1/2 | AWS has no fractional A10G type. |
| A10 | `Standard_NV72ads_A10_v5` | 2 | AWS G5 has no 2-GPU size. |
| RTX PRO 6000, fraction | NC RTX PRO 6000 BSE v6 | 1/4, 1/2 | AWS has no fractional RTX PRO 6000 type. |
| AMD Radeon PRO V620 | NGads V620 | 1/4 to 1 | AWS lists no V620. |
| AMD Radeon Pro V710 | NVads V710 v5 | 1/6 to 1 | AWS lists no V710. |

Notes:

- ND MI300X v5 links its GPUs with "4th-Gen AMD Infinity Fabric", 896 GB/s aggregate. It uses 400 Gbps Quantum-2 CX7 InfiniBand per GPU. ([Azure](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndmi300xv5-series))
- The NC A100 v4 page says that Azure deploys net new NC capacity only for the NCads_H100_v5 series. The note is older than the NC RTX PRO 6000 BSE v6 series. ([NC A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nca100v4-series), [Azure sizes overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview))

Sources: [Azure sizes overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview), [AWS spec](https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html), and the Azure series pages in the Sources list.

## Retired and previous-generation sizes

These sizes are out of the mapping and out of scope for probing.

- Azure retired the NCv3 series (V100) on 2025-09-30. ([Azure retirements](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/lifecycle/retirements-and-capacity-restrictions))
- Azure retired the NVv3 series (Tesla M60) and the NVv4 series (Instinct MI25) on 2026-09-30. ([NVv3](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvv3-series), [NVv4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvv4-series))
- AWS lists P3 and P3dn (V100) as previous-generation instance types. ([AWS previous generation](https://docs.aws.amazon.com/ec2/latest/instancetypes/pg.html))

## Limits of this note

- The Match column uses stated specs only. It does not use benchmarks or prices.
- A "Same" match does not mean the same CPU, system memory, or network. For example, 8x H100 uses EFA on AWS and InfiniBand on Azure.
- Region availability, quotas, and Spot support are out of scope. The note lists no Regions.

## Sources

- <https://docs.aws.amazon.com/ec2/latest/instancetypes/ac.html>
- <https://docs.aws.amazon.com/ec2/latest/instancetypes/pg.html>
- <https://aws.amazon.com/ec2/instance-types/p4/>
- <https://aws.amazon.com/ec2/instance-types/p5/>
- <https://aws.amazon.com/ec2/instance-types/p6/>
- <https://aws.amazon.com/about-aws/whats-new/2025/12/amazon-ec2-p6e-gb300-ultraservers-nvidia-gb300-nvl72-generally-available/>
- <https://aws.amazon.com/ec2/instance-types/g5/>
- <https://aws.amazon.com/ec2/instance-types/g5g/>
- <https://aws.amazon.com/ec2/instance-types/g7/>
- <https://aws.amazon.com/ec2/instance-types/g7e/>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndh100v5-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-h200-v5-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndasra100v4-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndma100v4-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb300-v6-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndmi300xv5-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncadsh100v5-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nccadsh100v5-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nca100v4-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series>
- <https://techcommunity.microsoft.com/blog/azurehighperformancecomputingblog/azure-ncv6-public-preview-the-new-unified-platform-for-converged-ai-and-visual-c/4472704>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncast4v3-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsa10v5-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsv710-v5-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ngadsv620-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvv3-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvv4-series>
- <https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/lifecycle/retirements-and-capacity-restrictions>
