---
kind: "concept"
title: "DriveLM-CARLA"
concept_id: 231
slug: "drivelm-carla"
node_type: "dataset"
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
compiled_at: "2026-04-26T16:52:40.877112+00:00"
compile_model: "gpt-4o-mini"
---

# DriveLM-CARLA

## 定义

DriveLM-CARLA 是一个用于训练与评测可扩展规则生成的驾驶问答和泛化的数据集。该数据集旨在通过视觉语言模型来提高自动驾驶系统的决策能力，尤其是在复杂驾驶场景下的表现。

## 不同视角

DriveLM-CARLA 的核心思想是将驾驶过程视作一个图形化的问答任务（GVQA），其中每个驾驶场景都被表示为一个有向无环图，节点代表问题与答案，边则表示问题之间的依赖关系[[paper:14]]。这种方法使得模型在做出驾驶决策时，可以参考先前问题的答案，从而形成更为系统化的决策过程。

在 DriveLM 的架构中，模型分为多个阶段：首先是感知阶段，识别场景中的关键物体和交通信号；接着是预测阶段，推断这些物体的运动和交互；最后是规划阶段，判断安全和危险的驾驶动作。这样的设计使得模型在生成自然语言描述时，能够综合考虑多个因素，形成更为合理的驾驶行为[[paper:14]]。

## 共识与分歧

目前，DriveLM-CARLA 在自动驾驶领域的应用上获得了一定的共识，尤其是在其通过问答形式提升决策能力方面。然而，关于如何进一步优化这一模型的性能和泛化能力仍存在分歧。一方面，研究者们普遍认可其在复杂场景下的有效性；另一方面，对于如何构建更为全面的问答体系和数据集的扩展性，仍需深入探讨和验证[[paper:14]]。

## 进一步阅读

有关 DriveLM-CARLA 的更多信息，可以参考相关文献，特别是关于其架构设计、评测指标以及零样本泛化的研究。
