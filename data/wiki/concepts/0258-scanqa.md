---
kind: "concept"
title: "ScanQA"
concept_id: 258
slug: "scanqa"
node_type: "dataset"
tags:
  - "Spatial-Visual-View Fusion"
  - "Egocentric Video"
  - "CUT3R"
  - "Temporal Reasoning"
  - "Vision-Language Model"
  - "Instruction Tuning"
  - "Spatial Reasoning"
  - "Large Multimodal Model"
  - "3D Reconstruction"
  - "Monocular Video"
source_paper_ids:
  - 3
compiled_at: "2026-04-26T16:57:45.957188+00:00"
compile_model: "gpt-4o-mini"
---

# ScanQA

## 定义

ScanQA是一个用于3D场景问答评测的数据集，旨在推动视觉-语言模型在空间理解和推理方面的能力。该数据集通过结合视频输入和语言指令，提供了一种新的方式来评估模型在复杂3D环境中的表现。

## 不同视角

在ScanQA的研究中，VLM-3R框架被提出，强调了将普通视觉模型与3D重建能力相结合的重要性。该框架通过引入空间编码器和视角信息，增强了模型对场景结构的理解，使其能够在回答问题时不仅依赖于2D视觉信息，还能利用3D空间常识。这种方法类似于为模型提供了一位能够在脑中绘制3D草图的向导，从而提升了其在复杂场景中的问答能力。

## 共识与分歧

研究者们普遍同意，结合空间信息和视觉信息对于提升问答系统的性能至关重要。然而，如何有效地实现这种结合仍存在分歧。一些研究者强调需要更多的空间上下文信息，而另一些则认为现有的2D视觉信息已足够。此外，关于如何优化模型架构以处理这些信息的具体方法也存在不同的看法。

## 进一步阅读

对于希望深入了解ScanQA及其相关技术的读者，可以参考VLM-3R框架的详细描述和实验结果，这些内容在相关论文中有深入探讨[[paper:3]]。
