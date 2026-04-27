---
kind: "concept"
title: "UniAD-Single"
concept_id: 237
slug: "uniad-single"
node_type: "technique"
tags:
  - "DriveLM-Data"
  - "Graph Visual Question Answering"
  - "nuScenes"
  - "CARLA"
  - "DriveLM-Agent"
  - "End-to-end Autonomous Driving"
  - "LoRA"
  - "trajectory tokenization"
  - "graph prompting"
  - "Vision-Language Model"
source_paper_ids:
  - 14
compiled_at: "2026-04-26T16:53:53.142161+00:00"
compile_model: "gpt-4o-mini"
---

# UniAD-Single

## 定义

UniAD-Single 是一种基线技术，主要用于处理与驾驶相关的视觉问答任务。其核心思想是通过图形化的问答结构来引导模型理解和预测驾驶场景中的动态变化。具体而言，UniAD-Single 通过将驾驶场景图像与一系列问题结合，形成一个有向无环图（DAG），每个节点代表一个问题及其答案，边则表示问题之间的依赖关系。

## 不同视角

在 UniAD-Single 的实现中，模型的工作流程被分为多个阶段，包括感知、预测和规划。感知阶段主要负责识别场景中的关键物体和交通标志；预测阶段则推断这些物体的运动和可能的交互；最后，在规划阶段，模型判断安全与危险的行为。这种分阶段的处理方式使得模型能够更系统地理解复杂的驾驶环境。

## 共识与分歧

在对 UniAD-Single 的研究中，学者们普遍认可其在驾驶场景理解中的有效性，尤其是在复杂环境下的表现。然而，关于其在不同驾驶场景中泛化能力的讨论仍存在分歧。虽然有研究验证了其零样本泛化能力，但在实际应用中，如何确保模型在各种未见场景下的稳定性仍是一个未解的问题。

## 进一步阅读

对于想深入了解 UniAD-Single 的读者，可以参考论文《DriveLM: Driving with Graph Visual Question Answering》，该论文详细介绍了该技术的原理、架构以及在驾驶任务中的应用。
