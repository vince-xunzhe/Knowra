---
kind: "concept"
title: "DriveLM-nuScenes"
concept_id: 230
slug: "drivelm-nuscenes"
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
compiled_at: "2026-04-26T16:52:26.126833+00:00"
compile_model: "gpt-4o-mini"
---

# DriveLM-nuScenes

## 定义

DriveLM-nuScenes 是一个专为图结构驾驶问答、行为预测和开环规划而设计的数据集。该数据集的核心理念是通过图视觉问答（GVQA）任务来提升自动驾驶系统的决策能力。DriveLM-nuScenes 旨在模拟新手司机在教练指导下的学习过程，通过一系列问题引导模型理解驾驶环境，从而做出更安全和合理的驾驶决策。

## 不同视角

在 DriveLM 的框架中，驾驶决策过程被分为多个阶段：感知、预测和规划。每个阶段都依赖于前一阶段的输出，形成一个有向无环图（DAG），其中每个节点代表一个问题及其答案，边则表示问题之间的依赖关系。这种结构使得模型能够在复杂的驾驶环境中进行有效的信息整合和决策。

## 共识与分歧

DriveLM-nuScenes 的设计得到了广泛的认可，尤其是在其提出的 GVQA 任务和数据构建方面。研究者们一致认为，这种基于问答的方式能够有效提升自动驾驶系统的理解能力和决策水平[[paper:14]]。然而，对于如何在实际应用中实现零样本泛化和评测指标的有效性，仍存在一定的争议。部分研究者认为现有的评测指标可能不足以全面反映模型在真实世界中的表现，呼吁进一步的研究和改进。

## 进一步阅读

有关 DriveLM-nuScenes 的详细信息和技术细节，可以参考相关文献，特别是《DriveLM: Driving with Graph Visual Question Answering》一文，该文详细介绍了 DriveLM 的架构、关键公式及其在自动驾驶中的应用。
