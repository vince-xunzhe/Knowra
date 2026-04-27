---
kind: "concept"
title: "DriveLM-Agent Chain"
concept_id: 241
slug: "drivelm-agent-chain"
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
compiled_at: "2026-04-26T16:54:36.660766+00:00"
compile_model: "gpt-4o-mini"
---

# DriveLM-Agent Chain

## 定义

DriveLM-Agent Chain 是一种基于图形视觉问答（GVQA）任务的驾驶辅助模型，旨在通过一系列结构化的问题和答案来指导自动驾驶决策。该模型的工作流程可以比作一个新手司机在教练的指导下进行驾驶，先通过回答一系列与驾驶场景相关的问题来获取必要的信息，再根据这些信息规划出安全的驾驶行为。

## 不同视角

DriveLM-Agent Chain 的架构分为多个阶段：首先在感知阶段（P1）识别关键物体和交通元素；接着在预测阶段（P2）推断这些元素的运动和交互；最后在规划阶段（P3）判断安全与危险的驾驶动作。每个阶段的问题和答案通过有向无环图（DAG）进行组织，形成一个复杂的知识网络，使得后续问题可以依赖于前一个问题的答案，从而增强决策的连贯性和准确性。

## 共识与分歧

在 DriveLM-Agent Chain 的研究中，学者们普遍认可其通过结构化问答提高自动驾驶决策的有效性。该模型的创新之处在于其将驾驶决策过程视为一个图形化的问答任务，显著提升了对复杂驾驶场景的理解能力[[paper:14]]。然而，关于如何进一步优化问答节点的设计和提升模型的实时响应能力，学术界仍存在不同看法。一些研究者认为，增加问题的多样性和复杂性可能会进一步提升模型的智能化水平，而另一些则担心这可能导致决策过程的延迟。

## 进一步阅读

对于希望深入了解 DriveLM-Agent Chain 的读者，可以参考相关文献，特别是关于 GVQA 任务的提出、DriveLM 数据集的构建以及驾驶评测指标的设计等方面的研究。这些文献为理解 DriveLM-Agent Chain 的理论基础和应用场景提供了重要的背景信息[[paper:14]]。
