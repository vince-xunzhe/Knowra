---
kind: "concept"
title: "DriveLM: Driving with G…"
concept_id: 228
slug: "drivelm-driving-with-g"
node_type: "paper"
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
compiled_at: "2026-04-26T16:52:06.903941+00:00"
compile_model: "gpt-4o-mini"
---

# DriveLM: Driving with G…

# DriveLM: Driving with Graph Visual Question Answering

## 定义
DriveLM 是一种将视觉语言模型应用于端到端自动驾驶的框架，旨在通过图式推理提升自动驾驶系统的能力。该模型通过构建一个问答图（Graph VQA），将感知、预测、规划和行为等步骤组织成带有依赖关系的问答结构。DriveLM-Agent 采用 BLIP-2 作为基础，结合 LoRA、图提示和轨迹分词技术，能够在复杂的驾驶场景中进行有效的决策。

## 不同视角
DriveLM 的设计理念可以比作一个新手司机在教练的指导下进行驾驶。与传统的自动驾驶模型相比，DriveLM 通过先回答一系列问题来引导决策过程，这些问题涉及周围环境的车辆和行人、潜在的危险动作以及当前的驾驶策略。这种方法使得模型在处理复杂场景时能够更为稳健，尤其是在未见传感器配置下的表现优于传统单帧模型。

## 共识与分歧
在 DriveLM 的研究中，学者们普遍认可其通过图式推理提升零样本泛化能力的潜力 [[paper:14]]。然而，对于如何最有效地构建和利用问答图的具体实现，仍存在一些分歧。一方面，DriveLM 提出的 GVQA 任务和问答节点的定义为未来的研究提供了新的方向；另一方面，如何在不同的驾驶场景中优化问答图的结构和内容，仍需进一步探索。

## 进一步阅读
- [DriveLM: Driving with Graph Visual Question Answering](https://arxiv.org/abs/2312.14150) - 原始论文，详细介绍了 DriveLM 的构建和实验结果。
