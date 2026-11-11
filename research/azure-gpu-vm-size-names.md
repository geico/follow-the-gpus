---
title: "Azure GPU VM size names"
date: 2026-10-11
---

# Azure GPU VM size names

## Summary

This note explains how Azure names its GPU VM sizes. It decodes each GPU size name in `config/targets.yaml`, [GPU lineup on AWS and Azure](gpu-lineup-aws-azure.md), and [AWS and Azure GPU equivalents](aws-azure-gpu-equivalents.md). All pages were read on 2026-10-11.

- **Formula.** Microsoft gives the pattern `[Family] + [Sub-family]* + [# of vCPUs] + [Constrained vCPUs]* + [Additive Features] + [Accelerator Type]* + [Memory Capacity]* + [Version]`. The page does not explain the asterisk. A part with an asterisk occurs only in some sizes. ([Azure naming conventions](https://learn.microsoft.com/en-us/azure/virtual-machines/vm-naming-conventions))
- **GPU families.** The first letters give the family: NC, ND, NG, or NV. ([Azure VM sizes overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview))
- **Number.** In all 43 GPU names in this note, the number after the family letters is the vCPU count on the series page.
- **Lowercase letters.** The lowercase letters after the number are additive features, for example `a` (AMD CPU), `d` (local temporary disk), `s` (Premium SSD), and `r` (RDMA). ([Azure naming conventions](https://learn.microsoft.com/en-us/azure/virtual-machines/vm-naming-conventions))
- **Accelerator.** Most GPU names have the GPU model between underscores, for example `_H100_`. NDv2 and ND A100 v4 names do not have it.
- **Rule gaps.** The letters do not always follow the rules. For example, the GB200 and GB300 names have no CPU letter, but their CPU is NVIDIA Grace, not Intel. The naming page and the series pages do not define the tokens `xl`, `NDR`, and `G5`. See [Where the names do not follow the rules](#where-the-names-do-not-follow-the-rules).

## Scope

This note covers Azure GPU VM sizes only, in the commercial cloud. It decodes the names. It does not give Region availability, prices, quotas, or Spot support. For GPU counts and GPU memory, see [GPU lineup on AWS and Azure](gpu-lineup-aws-azure.md).

## The naming formula

The Microsoft Learn naming page gives this pattern:

`[Family] + [Sub-family]* + [# of vCPUs] + [Constrained vCPUs]* + [Additive Features] + [Accelerator Type]* + [Memory Capacity]* + [Version]`

([Azure naming conventions](https://learn.microsoft.com/en-us/azure/virtual-machines/vm-naming-conventions))

| Part | Meaning on the naming page | In GPU names |
|---|---|---|
| Family | "Indicates the VM Family Series." | `N` |
| Sub-family | "Used for specialized VM differentiations". Has an asterisk. | `C`, `D`, `G`, or `V`. NCC has 2 sub-family letters. |
| # of vCPUs | "Denotes the number of vCPUs of the VM". | For example `96` |
| Constrained vCPUs | "Used for certain VM sizes only." Has an asterisk. | Not used in the GPU names of this note |
| Additive features | "Lower case letters denote additive features". | For example `isr`, `ads` |
| Accelerator type | "Denotes the type of hardware accelerator in the specialized/GPU SKUs." Has an asterisk. | For example `H100`, `A10`, `T4` |
| Memory capacity | "Denotes M-series memory capacity, rounded to the nearest TiB." Has an asterisk. | Not used in GPU names |
| Version | "Denotes the version number of the VM Family Series". | For example `v5` |

Source for each row: [Azure naming conventions](https://learn.microsoft.com/en-us/azure/virtual-machines/vm-naming-conventions).

The naming page also says: "Only new specialized/GPU SKUs launched from Q3 2020 have the hardware accelerator in the name." ([Azure naming conventions](https://learn.microsoft.com/en-us/azure/virtual-machines/vm-naming-conventions))

The page gives `NC4as_T4_v3` as an example: family `N`, sub-family `C`, 4 vCPUs, `a` (AMD-based processor), `s` (Premium Storage capable), accelerator `T4`, version `v3`. ([Azure naming conventions](https://learn.microsoft.com/en-us/azure/virtual-machines/vm-naming-conventions))

Some parts without an asterisk can also be absent. See [Rules from the sizes overview](#rules-from-the-sizes-overview).

The pattern does not include the `Standard_` prefix. All sizes in this note have it.

### Sub-family letters

The naming page lists these sub-family letters. This table gives only the letters that occur in GPU names.

| Letter | Meaning | GPU series that use it |
|---|---|---|
| `C` | "compute intensive (for HC, NC, NCC series)" | NC, NCC |
| `C` | "confidential (for DC, EC, NCC series)" | NCC |
| `D` | "AI training and inference optimized" | ND |
| `G` | "cloud gaming and remote desktop optimized" | NG |
| `V` | "visualization and graphics optimized" | NV |

Source: [Azure naming conventions](https://learn.microsoft.com/en-us/azure/virtual-machines/vm-naming-conventions).

### Additive feature letters

The naming page lists these lowercase letters. ([Azure naming conventions](https://learn.microsoft.com/en-us/azure/virtual-machines/vm-naming-conventions))

| Letter | Meaning on the naming page | In GPU names of this note |
|---|---|---|
| `a` | AMD-based processor | Yes |
| `b` | remote storage bandwidth optimized | No |
| `d` | includes local temporary disks | Yes |
| `e` | encrypted; contains confidential TDX capabilities | No |
| `f` | flat ratio (1:1) of vCPU to memory size | No |
| `i` | isolated size | Yes |
| `l` | low memory; decreased vCPU to memory ratio | Yes |
| `m` | memory intensive; highest vCPU to memory ratio in a particular series | Yes |
| `n` | network optimized; increased vCPU to network bandwidth ratio | No |
| `o` | increased vCPU to local SSD storage capacity ratio | No |
| `p` | ARM-based processor | No |
| `r` | includes RDMA (InfiniBand) secondary network | Yes |
| `s` | compatible with any Premium SSD type | Yes |
| `t` | tiny memory; smallest vCPU to memory ratio in a particular size | No |

For `s`, the formula table says "compatible with any Premium SSD type". The examples on the same page say "Premium Storage capable". ([Azure naming conventions](https://learn.microsoft.com/en-us/azure/virtual-machines/vm-naming-conventions))

### Rules from the sizes overview

The sizes overview page adds these rules ([Azure VM sizes overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview)):

- "Most families are represented using one letter, but others such as GPU sizes (ND-series, NV-series, etc.) use two."
- "If no feature letter for a CPU is listed, the series uses Intel x86-64 CPUs. If the CPU is AMD, it's listed as a. If the CPU is ARM based (Microsoft Cobalt or Ampere Altra), it's listed as p."
- "Spacers can show up multiple times in a size name such as in the ND_H100_v5-series. In this case they separate the GPU ID from the rest of the size name."
- "Version numbers only appear in the size name if there are multiple versions of the same series. If you're using the first version of a series (HB-series, B-series, etc.) it's often not included in the size name."

## Decode table

Each row splits 1 size name into its parts. "vCPUs" is the number in the name. It equals the vCPU count in the "vCPUs (Qty.)" column of the series page for every row. "CPU" comes from the "Processor" row of the series page host specifications table. The source of each row is the series page in the last column.

Feature letters: `a` AMD CPU, `d` local temporary disk, `i` isolated size, `l` low memory, `m` memory intensive, `r` RDMA (InfiniBand), `s` Premium SSD.

### ND family

| Size name | Family and sub-family | vCPUs | Features | Accelerator | Other token | Version | CPU | Source |
|---|---|---|---|---|---|---|---|---|
| `Standard_ND128isr_GB300_v6` | N + D | 128 | `i` `s` `r` | `GB300` | None | v6 | NVIDIA Grace | [ND GB300 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb300-v6-series) |
| `Standard_ND128isrG5_GB300_v6` | N + D | 128 | `i` `s` `r` | `GB300` | `G5` (not defined) | v6 | NVIDIA Grace | [ND GB300 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb300-v6-series) |
| `Standard_ND128isr_NDR_GB200_v6` | N + D | 128 | `i` `s` `r` | `GB200` | `NDR` (not defined) | v6 | NVIDIA Grace | [ND GB200 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series) |
| `Standard_ND96isr_H200_v5` | N + D | 96 | `i` `s` `r` | `H200` | None | v5 | Intel Xeon (Sapphire Rapids) | [ND H200 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-h200-v5-series) |
| `Standard_ND96isr_H100_v5` | N + D | 96 | `i` `s` `r` | `H100` | None | v5 | Intel Xeon (Sapphire Rapids) | [ND H100 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndh100v5-series) |
| `Standard_ND96isr_MI300X_v5` | N + D | 96 | `i` `s` `r` | `MI300X` | None | v5 | Intel Xeon (Sapphire Rapids) | [ND MI300X v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndmi300xv5-series) |
| `Standard_ND96amsr_A100_v4` | N + D | 96 | `a` `m` `s` `r` | `A100` | None | v4 | AMD EPYC 7V12 (Rome) | [NDm A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndma100v4-series) |
| `Standard_ND96asr_v4` | N + D | 96 | `a` `s` `r` | None in the name. The series page gives A100 40 GB. | None | v4 | AMD EPYC 7V12 (Rome) | [ND A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndasra100v4-series) |
| `Standard_ND40rs_v2` | N + D | 40 | `r` `s` | None in the name. The series page gives Tesla V100. | None | v2 | Intel Xeon Platinum 8168 (Skylake) | [NDv2](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndv2-series) |

### NC family

`RTXPRO6000BSE` is the RTX PRO 6000 Blackwell Server Edition GPU. The series page names this GPU. ([NC RTX PRO 6000 BSE v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series))

| Size name | Family and sub-family | vCPUs | Features | Accelerator | Other token | Version | CPU | Source |
|---|---|---|---|---|---|---|---|---|
| `Standard_NC36ds_xl_RTXPRO6000BSE_v6` | N + C | 36 | `d` `s` | `RTXPRO6000BSE` | `xl` (not defined) | v6 | Intel Xeon Granite Rapids-AP | [NC RTX PRO 6000 BSE v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series) |
| `Standard_NC72ds_xl_RTXPRO6000BSE_v6` | N + C | 72 | `d` `s` | `RTXPRO6000BSE` | `xl` (not defined) | v6 | Intel Xeon Granite Rapids-AP | [NC RTX PRO 6000 BSE v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series) |
| `Standard_NC144ds_xl_RTXPRO6000BSE_v6` | N + C | 144 | `d` `s` | `RTXPRO6000BSE` | `xl` (not defined) | v6 | Intel Xeon Granite Rapids-AP | [NC RTX PRO 6000 BSE v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series) |
| `Standard_NC288ds_xl_RTXPRO6000BSE_v6` | N + C | 288 | `d` `s` | `RTXPRO6000BSE` | `xl` (not defined) | v6 | Intel Xeon Granite Rapids-AP | [NC RTX PRO 6000 BSE v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series) |
| `Standard_NC24lds_xl_RTXPRO6000BSE_v6` | N + C | 24 | `l` `d` `s` | `RTXPRO6000BSE` | `xl` (not defined) | v6 | Intel Xeon Granite Rapids-AP | [NC RTX PRO 6000 BSE v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series) |
| `Standard_NC36lds_xl_RTXPRO6000BSE_v6` | N + C | 36 | `l` `d` `s` | `RTXPRO6000BSE` | `xl` (not defined) | v6 | Intel Xeon Granite Rapids-AP | [NC RTX PRO 6000 BSE v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series) |
| `Standard_NC72lds_xl_RTXPRO6000BSE_v6` | N + C | 72 | `l` `d` `s` | `RTXPRO6000BSE` | `xl` (not defined) | v6 | Intel Xeon Granite Rapids-AP | [NC RTX PRO 6000 BSE v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series) |
| `Standard_NC144lds_xl_RTXPRO6000BSE_v6` | N + C | 144 | `l` `d` `s` | `RTXPRO6000BSE` | `xl` (not defined) | v6 | Intel Xeon Granite Rapids-AP | [NC RTX PRO 6000 BSE v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series) |
| `Standard_NC288lds_xl_RTXPRO6000BSE_v6` | N + C | 288 | `l` `d` `s` | `RTXPRO6000BSE` | `xl` (not defined) | v6 | Intel Xeon Granite Rapids-AP | [NC RTX PRO 6000 BSE v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series) |
| `Standard_NC40ads_H100_v5` | N + C | 40 | `a` `d` `s` | `H100` | None | v5 | AMD EPYC (Genoa) | [NCads H100 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncadsh100v5-series) |
| `Standard_NC80adis_H100_v5` | N + C | 80 | `a` `d` `i` `s` | `H100` | None | v5 | AMD EPYC (Genoa) | [NCads H100 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncadsh100v5-series) |
| `Standard_NCC40ads_H100_v5` | N + C (compute intensive) + C (confidential) | 40 | `a` `d` `s` | `H100` | None | v5 | AMD EPYC (Genoa) | [NCCads H100 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nccadsh100v5-series) |
| `Standard_NC24ads_A100_v4` | N + C | 24 | `a` `d` `s` | `A100` | None | v4 | AMD EPYC 7V13 (Milan) | [NC A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nca100v4-series) |
| `Standard_NC48ads_A100_v4` | N + C | 48 | `a` `d` `s` | `A100` | None | v4 | AMD EPYC 7V13 (Milan) | [NC A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nca100v4-series) |
| `Standard_NC96ads_A100_v4` | N + C | 96 | `a` `d` `s` | `A100` | None | v4 | AMD EPYC 7V13 (Milan) | [NC A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nca100v4-series) |
| `Standard_NC4as_T4_v3` | N + C | 4 | `a` `s` | `T4` | None | v3 | AMD EPYC 7V12 (Rome) | [NCasT4_v3](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncast4v3-series) |
| `Standard_NC8as_T4_v3` | N + C | 8 | `a` `s` | `T4` | None | v3 | AMD EPYC 7V12 (Rome) | [NCasT4_v3](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncast4v3-series) |
| `Standard_NC16as_T4_v3` | N + C | 16 | `a` `s` | `T4` | None | v3 | AMD EPYC 7V12 (Rome) | [NCasT4_v3](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncast4v3-series) |
| `Standard_NC64as_T4_v3` | N + C | 64 | `a` `s` | `T4` | None | v3 | AMD EPYC 7V12 (Rome) | [NCasT4_v3](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncast4v3-series) |

### NV family

| Size name | Family and sub-family | vCPUs | Features | Accelerator | Other token | Version | CPU | Source |
|---|---|---|---|---|---|---|---|---|
| `Standard_NV6ads_A10_v5` | N + V | 6 | `a` `d` `s` | `A10` | None | v5 | AMD EPYC 74F3v (Milan) | [NVads A10 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsa10v5-series) |
| `Standard_NV12ads_A10_v5` | N + V | 12 | `a` `d` `s` | `A10` | None | v5 | AMD EPYC 74F3v (Milan) | [NVads A10 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsa10v5-series) |
| `Standard_NV18ads_A10_v5` | N + V | 18 | `a` `d` `s` | `A10` | None | v5 | AMD EPYC 74F3v (Milan) | [NVads A10 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsa10v5-series) |
| `Standard_NV36ads_A10_v5` | N + V | 36 | `a` `d` `s` | `A10` | None | v5 | AMD EPYC 74F3v (Milan) | [NVads A10 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsa10v5-series) |
| `Standard_NV36adms_A10_v5` | N + V | 36 | `a` `d` `m` `s` | `A10` | None | v5 | AMD EPYC 74F3v (Milan) | [NVads A10 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsa10v5-series) |
| `Standard_NV72ads_A10_v5` | N + V | 72 | `a` `d` `s` | `A10` | None | v5 | AMD EPYC 74F3v (Milan) | [NVads A10 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsa10v5-series) |
| `Standard_NV4ads_V710_v5` | N + V | 4 | `a` `d` `s` | `V710` | None | v5 | AMD EPYC 9V64 F (Genoa) | [NVads V710 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsv710-v5-series) |
| `Standard_NV8ads_V710_v5` | N + V | 8 | `a` `d` `s` | `V710` | None | v5 | AMD EPYC 9V64 F (Genoa) | [NVads V710 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsv710-v5-series) |
| `Standard_NV12ads_V710_v5` | N + V | 12 | `a` `d` `s` | `V710` | None | v5 | AMD EPYC 9V64 F (Genoa) | [NVads V710 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsv710-v5-series) |
| `Standard_NV24ads_V710_v5` | N + V | 24 | `a` `d` `s` | `V710` | None | v5 | AMD EPYC 9V64 F (Genoa) | [NVads V710 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsv710-v5-series) |
| `Standard_NV28adms_V710_v5` | N + V | 28 | `a` `d` `m` `s` | `V710` | None | v5 | AMD EPYC 9V64 F (Genoa) | [NVads V710 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsv710-v5-series) |

### NG family

| Size name | Family and sub-family | vCPUs | Features | Accelerator | Other token | Version | CPU | Source |
|---|---|---|---|---|---|---|---|---|
| `Standard_NG8ads_V620_v1` | N + G | 8 | `a` `d` `s` | `V620` | None | v1 | AMD EPYC 7763 (Milan) | [NGads V620](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ngadsv620-series) |
| `Standard_NG16ads_V620_v1` | N + G | 16 | `a` `d` `s` | `V620` | None | v1 | AMD EPYC 7763 (Milan) | [NGads V620](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ngadsv620-series) |
| `Standard_NG32ads_V620_v1` | N + G | 32 | `a` `d` `s` | `V620` | None | v1 | AMD EPYC 7763 (Milan) | [NGads V620](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ngadsv620-series) |
| `Standard_NG32adms_V620_v1` | N + G | 32 | `a` `d` `m` `s` | `V620` | None | v1 | AMD EPYC 7763 (Milan) | [NGads V620](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ngadsv620-series) |

## Where the names do not follow the rules

The series pages confirm most letters. These cases do not match the rules.

### Family: 1 letter or 2

The naming page splits `NC4as_T4_v3` into family `N` and sub-family `C`. The sizes overview says that GPU families "use two" letters, for example ND and NV. ([Azure naming conventions](https://learn.microsoft.com/en-us/azure/virtual-machines/vm-naming-conventions), [Azure VM sizes overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview)) The first sentence of the NC family page calls NC "The 'NC' sub-family". The first sentence of the ND family page calls ND "The 'ND' family". ([NC family](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-family), [ND family](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-family)) This note writes "N + C" to follow the naming page.

### CPU letter

- **`a` is the CPU, not the GPU.** `Standard_ND96isr_MI300X_v5` has an AMD Instinct MI300X GPU, but no `a`. Its CPU is Intel Xeon (Sapphire Rapids). ([ND MI300X v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndmi300xv5-series))
- **Grace has no letter.** The GB200 and GB300 names have no `a` and no `p`. By the overview rule, no CPU letter means Intel x86-64. But the series pages give "Nvidia Grace CPU". ([Azure VM sizes overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview), [ND GB200 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series), [ND GB300 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb300-v6-series)) NVIDIA says that the Grace CPU Superchip "packs 144 Arm Neoverse V2 cores". ([NVIDIA Grace CPU](https://www.nvidia.com/en-us/data-center/grace-cpu/)) The overview names only "Microsoft Cobalt or Ampere Altra" for `p`.

### Local disk letter `d`

No ND name in this note has a `d`. But the "Local (temp) storage" table of each ND series page lists temp disks:

- `Standard_ND128isr_GB300_v6` and `Standard_ND128isrG5_GB300_v6`: 4 temp disks, size 16 TB. ([ND GB300 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb300-v6-series))
- `Standard_ND128isr_NDR_GB200_v6`: 4 temp disks, size 16 TB. ([ND GB200 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series))
- `Standard_ND96isr_H200_v5`: 1 temp disk of 1,024 GiB. ([ND H200 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-h200-v5-series))
- `Standard_ND96isr_H100_v5`: 1 temp disk of 1,024 GiB. ([ND H100 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndh100v5-series))
- `Standard_ND96isr_MI300X_v5`: 1 temp disk of 1,000 GiB. ([ND MI300X v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndmi300xv5-series))
- `Standard_ND96amsr_A100_v4`: 1 temp disk of 6,400 GiB. ([NDm A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndma100v4-series))
- `Standard_ND96asr_v4`: 1 temp disk of 6,000 GiB. ([ND A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndasra100v4-series))
- `Standard_ND40rs_v2`: 1 temp disk of 2,948 GiB. ([NDv2](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndv2-series))

All NC, NV, and NG names in this note have a `d`, except the 4 NCasT4_v3 names. The NCasT4_v3 page also lists 1 temp disk for each size. ([NCasT4_v3](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ncast4v3-series))

### Isolated letter `i`

The Azure isolation page lists the isolated sizes. Its list does not match the `i` letter ([Azure isolation](https://learn.microsoft.com/en-us/azure/virtual-machines/isolation)):

- **`i` and on the list:** `Standard_ND96isr_H100_v5`, `Standard_ND96isr_H200_v5`, `Standard_ND96isr_MI300X_v5`, `Standard_ND128isr_NDR_GB200_v6`, and `Standard_NC80adis_H100_v5`.
- **`i` but not on the list:** `Standard_ND128isr_GB300_v6` and `Standard_ND128isrG5_GB300_v6`. The list has `Standard_ND128isr_NDR_GB300_v6`. The ND GB300 v6 series page does not list that name. ([ND GB300 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb300-v6-series))
- **On the list but no `i`:** `Standard_NC64as_T4_v3`, `Standard_NC96ads_A100_v4`, `Standard_NC288ds_xl_RTXPRO6000BSE_v6`, `Standard_NC288lds_xl_RTXPRO6000BSE_v6`, `Standard_NG32ads_V620_v1`, `Standard_NG32adms_V620_v1`, and `Standard_NV72ads_A10_v5`. The isolation page FAQ expects this case. For an isolated size without `i`, only the isolation feature retires. A size with `i` retires entirely.

### Memory letters `l` and `m`

The series pages agree with `l` and `m`. A size with `l` has less memory than the size with the same vCPU count and no `l`. A size with `m` has more memory. Values are from the "Memory (GiB)" column of each series page.

| Pair | Memory without the letter | Memory with the letter | Source |
|---|---|---|---|
| `NC36ds_xl` and `NC36lds_xl` | 132 GiB | 72 GiB | [NC RTX PRO 6000 BSE v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series) |
| `NC288ds_xl` and `NC288lds_xl` | 1,032 GiB | 516 GiB | [NC RTX PRO 6000 BSE v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series) |
| `ND96asr_v4` and `ND96amsr_A100_v4` | 900 GiB | 1,900 GiB | [ND A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndasra100v4-series), [NDm A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndma100v4-series) |
| `NV36ads_A10_v5` and `NV36adms_A10_v5` | 440 GiB | 880 GiB | [NVads A10 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsa10v5-series) |
| `NG32ads_V620_v1` and `NG32adms_V620_v1` | 64 GiB | 176 GiB | [NGads V620](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ngadsv620-series) |

`Standard_NV28adms_V710_v5` has no partner size with 28 vCPUs and no `m`. It has 160 GiB. ([NVads V710 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nvadsv710-v5-series))

### RDMA letter `r`

Each name with `r` belongs to a series whose page describes an InfiniBand network: EDR on NDv2, HDR on ND A100 v4 and NDm A100 v4, and Quantum-2 CX7 or 800 Gbps InfiniBand on the newer ND series. ([NDv2](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndv2-series), [ND A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndasra100v4-series), [NDm A100 v4](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndma100v4-series), [ND H100 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndh100v5-series), [ND H200 v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-h200-v5-series), [ND MI300X v5](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ndmi300xv5-series), [ND GB200 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series), [ND GB300 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb300-v6-series))

### Premium SSD letter `s`

All 43 names have `s`. Each series page gives "Premium Storage" as "Supported" in its feature support table. (Series pages in the decode table)

### Accelerator type

`Standard_ND40rs_v2` and `Standard_ND96asr_v4` have no accelerator type in the name. The naming page puts the accelerator in the name only for GPU SKUs "launched from Q3 2020". ([Azure naming conventions](https://learn.microsoft.com/en-us/azure/virtual-machines/vm-naming-conventions)) This rule does not explain `Standard_ND96asr_v4`. Microsoft announced ND A100 v4 general availability on 2021-06-01, after Q3 2020. ([Azure blog, ND A100 v4 GA](https://azure.microsoft.com/en-us/blog/azure-announces-general-availability-of-scaleup-scaleout-nvidia-a100-gpu-instances-claims-title-of-fastest-public-cloud-super/)) The other 41 names have an accelerator type.

### Version

The overview says that the first version of a series is "often not included in the size name". The NGads V620 names end in `_v1`. ([Azure VM sizes overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview), [NGads V620](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/ngadsv620-series))

## Gaps

- **`xl`.** Neither the naming page nor the NC RTX PRO 6000 BSE v6 series page defines `xl`. The series page puts all 9 sizes in 2 groups: "General Purpose Sizes" (`ds_xl`) and "Compute Optimized Sizes" (`lds_xl`). ([NC RTX PRO 6000 BSE v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-rtxpro6000-bse-v6-series))
- **`NDR`.** Neither the naming page nor the ND GB200 v6 series page defines `NDR`. The series page gives "4× 400 GB/s NVIDIA Quantum-2 CX7 InfiniBand connections per VM". ([ND GB200 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb200-v6-series)) NVIDIA uses NDR for "400 Gbps (NDR) InfiniBand". ([NVIDIA NDR overview](https://docs.nvidia.com/dgx-superpod/design-guide-cabling-data-centers/latest/ndr-overview.html)) Thus `NDR` probably names the InfiniBand generation. This is a judgment of this note, not a Microsoft statement.
- **`G5`.** Neither the naming page nor the ND GB300 v6 series page defines `G5`. The series page gives the same vCPUs, memory, temp storage, remote storage, and network values for `Standard_ND128isr_GB300_v6` and `Standard_ND128isrG5_GB300_v6`. ([ND GB300 v6](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-gb300-v6-series))
- **`Standard_` prefix.** The naming page pattern does not explain the `Standard_` prefix.

## Sources

- [Azure VM sizes naming conventions](https://learn.microsoft.com/en-us/azure/virtual-machines/vm-naming-conventions)
- [Azure VM sizes overview](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/overview)
- [Azure isolation for VMs](https://learn.microsoft.com/en-us/azure/virtual-machines/isolation)
- [Azure blog: ND A100 v4 general availability (2021-06-01)](https://azure.microsoft.com/en-us/blog/azure-announces-general-availability-of-scaleup-scaleout-nvidia-a100-gpu-instances-claims-title-of-fastest-public-cloud-super/)
- [Azure NC family](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nc-family)
- [Azure ND family](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/gpu-accelerated/nd-family)
- Azure GPU series pages, linked in each decode table row
- [NVIDIA Grace CPU](https://www.nvidia.com/en-us/data-center/grace-cpu/)
- [NVIDIA DGX SuperPOD NDR overview](https://docs.nvidia.com/dgx-superpod/design-guide-cabling-data-centers/latest/ndr-overview.html)
