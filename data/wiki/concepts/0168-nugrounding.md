---
kind: "concept"
title: "NuGrounding"
concept_id: 168
slug: "nugrounding"
node_type: "dataset"
tags:
  - "BEV特征"
  - "多模态大语言模型"
  - "自动驾驶"
  - "多视角3D视觉定位"
  - "NuScenes"
  - "融合解码器"
  - "3D边界框回归"
  - "上下文查询"
  - "对象查询"
  - "Hierarchy of Grounding"
source_paper_ids:
  - 12
compiled_at: "2026-04-26T16:43:08.045026+00:00"
compile_model: "gpt-4o-mini"
---

# NuGrounding

# NuGrounding

## 定义
NuGrounding是一个用于自动驾驶领域的多视角3D视觉定位数据集，包含约220万条文本提示和34149帧图像，平均每条提示对应3.7个目标。该数据集旨在结合多模态输入，提升机器学习模型在复杂场景下的理解与定位能力。

## 不同视角
在NuGrounding的框架中，模型的工作原理可以比作“一个懂交通规则的导航员”和“一个眼神很准的侦察员”的合作。具体而言，MLLM（多模态大语言模型）负责理解复杂的文本指令，如“前右方停着的黑色车”，而3D检测器则负责从多视角图像中提取候选物体的位置。通过将MLLM的理解与3D检测器的空间信息结合，NuGrounding实现了对目标的精确定位。

## 共识与分歧
在对NuGrounding的研究中，学者们普遍认可其在自动驾驶领域的重要性，尤其是在提高多模态理解和3D定位精度方面的贡献。该数据集不仅提供了丰富的训练和评测素材，还建立了一个新的多视角3D定位基准。然而，关于如何优化MLLM与3D检测器的融合方式，学术界仍存在一定的分歧。一些研究者建议进一步探索更复杂的上下文查询机制，以增强模型的表现，而另一些则关注于如何提升数据集的多样性和覆盖面。

## 进一步阅读
对于希望深入了解NuGrounding及其应用的读者，可以参考以下论文：“NuGrounding: A Multi-View 3D Visual Grounding Framework in Autonomous Driving”[[paper:12]]，该论文详细介绍了数据集的构建、模型架构以及实验结果。
