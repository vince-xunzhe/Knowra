---
kind: "concept"
title: "UniAD"
concept_id: 236
slug: "uniad"
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
compiled_at: "2026-04-26T16:53:43.253988+00:00"
compile_model: "gpt-4o-mini"
---

# UniAD

## 定义

UniAD（Unified Action Decision）是一种基于图形视觉问答（GVQA）任务的技术，旨在提升自动驾驶系统的决策能力。其核心思想是通过一系列结构化的问题和答案来指导驾驶行为的决策过程。具体而言，UniAD 将驾驶场景视为一个有向无环图，其中每个节点代表一个问题及其答案，而边则表示问题之间的依赖关系。这种方法使得模型在做出决策时，可以参考先前的问题和答案，从而形成一个连贯的决策链。

## 不同视角

在 UniAD 的实现中，DriveLM 作为其具体应用，展现了其在自动驾驶领域的潜力。该模型通过三个阶段的处理流程来实现其目标：感知阶段（P1）识别关键物体和交通标志，预测阶段（P2）推断物体的运动和交互，规划阶段（P3）判断安全与危险的驾驶动作。这种分阶段的处理方式使得模型能够在复杂的驾驶环境中作出更为精准的决策。

## 共识与分歧

在当前的研究中，UniAD 的设计理念得到了广泛认可，尤其是在其通过问答结构来增强决策过程的有效性方面。许多研究者认为，这种方法能够有效地整合视觉信息与语言理解，从而提升自动驾驶的安全性和可靠性[[paper:14]]。然而，关于如何优化问答节点的设计和依赖关系的构建，仍然存在不同的看法。一些学者提出需要进一步探索如何在不同场景下调整问题的优先级和重要性，以提高模型的适应性和泛化能力。

## 进一步阅读

对于想要深入了解 UniAD 及其在自动驾驶中的应用的读者，可以参考以下文献：
- "DriveLM: Driving with Graph Visual Question Answering" [[paper:14]]，该论文详细介绍了 UniAD 的原理、架构及其在自动驾驶中的具体实现。
