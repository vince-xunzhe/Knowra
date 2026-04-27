---
kind: "concept"
title: "Command Mean"
concept_id: 235
slug: "command-mean"
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
compiled_at: "2026-04-26T16:53:26.265625+00:00"
compile_model: "gpt-4o-mini"
---

# Command Mean

## 定义

Command Mean 是一种基于图形视觉问答（GVQA）任务的技术，旨在提升自动驾驶系统的决策能力。该技术通过将驾驶场景中的关键问答组织成一个有向无环图（DAG），使得每个问题的答案可以依赖于前一个问题的答案，从而形成一个任务清单。这种方法类似于新手司机在教练的指导下，通过回答一系列问题来做出更安全的驾驶决策。

## 不同视角

在 DriveLM 的框架中，输入包括一帧驾驶场景图像和当前要回答的问题。系统分为多个阶段：感知阶段（P1）负责识别场景中的关键物体和交通标志；预测阶段（P2）推断这些物体的运动和交互；规划阶段（P3）判断安全与危险的动作。最终，系统将这些信息汇总，生成自然语言的驾驶行为，并通过轨迹分词器输出未来的行驶路径。

## 共识与分歧

DriveLM 提出的 GVQA 任务和其架构设计得到了广泛认可，尤其是在提升自动驾驶系统的智能决策能力方面。研究者们一致认为，通过将问答节点构建为图形结构，可以有效地处理复杂的驾驶环境。然而，对于如何优化问答节点的设计和图的构建方式，仍存在不同的看法。一些研究者认为，增加更多的上下文信息可以进一步提高决策的准确性，而另一些则关注于简化模型以提升实时性。

## 进一步阅读

有关 Command Mean 的详细信息和应用实例，可以参考论文《DriveLM: Driving with Graph Visual Question Answering》，该论文提出了 GVQA 任务的概念，构建了 DriveLM 数据集，并设计了 DriveLM-Agent 以验证其有效性。
