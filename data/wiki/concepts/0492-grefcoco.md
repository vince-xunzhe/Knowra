---
kind: "concept"
title: "gRefCOCO"
aliases:
  - "concept:492"
  - "grefcoco"
  - "gRefCOCO"
concept_id: 492
slug: "grefcoco"
node_type: "dataset"
concept_origin: "auto"
tags:
  - "SAM-free segmentation"
  - "box-guided mask decoder"
  - "open-world referring segmentation"
  - "ORS-Bench"
  - "vision-language grounding"
  - "Qwen3-VL"
  - "referring segmentation"
  - "pixel-level mask prediction"
  - "SA1B-ORS"
  - "MLLM"
source_paper_ids:
  - 26
compiled_at: "2026-05-13T12:02:46.165741+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "bbaaf89e671abfc23b51dadef6698b5a6a09aee4"
---

# gRefCOCO

## 定义

gRefCOCO 是一个与指代分割相关的 benchmark，在材料中主要作为对比基准出现，用来体现开放性与多目标设置下的评测差异。它关联的问题是：给定图像和自然语言指代表达，模型需要定位并分割出被描述的目标区域。

在 Qwen3-VL-Seg 的语境中，gRefCOCO 被放在开放世界指代分割的大背景下理解：传统多模态大模型往往更擅长输出目标框，而像素级分割需要更精细的边界建模；同时，开放自然语言描述、多实例类别目标和 OOD 场景会进一步增加评测难度 [[paper:26]]。

## 与开放世界指代分割的关系

gRefCOCO 的意义不在于单独定义一种新任务，而在于作为相关 benchmark 帮助衡量模型在更复杂指代场景中的能力。Qwen3-VL-Seg 论文强调，开放世界指代分割要求模型理解任意自然语言描述，并输出精细 mask，而不仅是检测框 [[paper:26]]。

该论文提出的 Qwen3-VL-Seg 通过先由 Qwen3-VL 预测目标 bbox，再用轻量 mask decoder 将框先验转换为像素级分割结果。这种方法与 gRefCOCO 所代表的开放性、多目标评测需求相关：模型不仅要知道“目标大概在哪里”，还要在多个可能实例或复杂描述中确定“具体是哪一个/哪些区域” [[paper:26]]。

## 当前材料中的定位

根据现有材料，gRefCOCO 主要被描述为一个“相关 benchmark”，用于对比开放性和多目标设置。材料没有提供其具体规模、标注格式、类别范围或评测指标，因此不能进一步展开数据集构成。

可确认的是，Qwen3-VL-Seg 论文将这类 benchmark 放在开放世界 referring segmentation 的评测体系中，并进一步构建了 ORS-Bench 来评估开放词汇、复杂描述和 OOD 场景下的模型能力 [[paper:26]]。gRefCOCO 因而可被理解为该领域中用于参照或比较的一类基准之一。
