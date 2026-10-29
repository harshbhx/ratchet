# NOTICE

This folder holds a LoRA adapter trained with Ratchet (ratchet 0.5.0). The licenses below travel with it: keep this file with the adapter wherever you copy, share or deploy it.

## Base model

The adapter runs on top of `nvidia/NVIDIA-Nemotron-3-Nano-4B-BF16`, which is governed by the NVIDIA Nemotron Open Model License: https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-nemotron-open-model-license/

## Training data

The adapter was trained on teacher outputs only: answers written by the teacher models below. The student's own answers were never training targets.

| Teacher | License | License URL | Examples |
|---|---|---|---|
| nvidia/Nemotron-3-Ultra-550b-a55b | OpenMDW-1.1 | https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16 | 3864 |

The bill of materials (ratchet_bom.json) lists the exact training and golden sets, their hashes and the evaluation; check it with `python -m ratchet.bom verify`.

Not legal advice: check each license yourself.
