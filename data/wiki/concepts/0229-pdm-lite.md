---
kind: "concept"
title: "PDM-Lite"
concept_id: 229
slug: "pdm-lite"
node_type: "technique"
tags:
  - "DriveLM-Data"
  - "rule-based expert"
  - "Graph Visual Question Answering"
  - "nuScenes"
  - "CARLA"
  - "DriveLM-Agent"
  - "End-to-end Autonomous Driving"
  - "LoRA"
  - "轻量规则专家"
  - "trajectory tokenization"
  - "graph prompting"
  - "Vision-Language Model"
source_paper_ids:
  - 14
compiled_at: "2026-04-26T16:52:16.320724+00:00"
compile_model: "gpt-4o-mini"
---

# PDM-Lite

## 定义

PDM-Lite（CARLA标注专家）是一种用于自动驾驶场景理解和决策的技术，旨在通过图形视觉问答（GVQA）来增强驾驶模型的智能决策能力。该技术的核心理念是将驾驶过程视为一系列问题的回答过程，类似于新手司机在教练的指导下进行驾驶。具体来说，PDM-Lite 通过识别场景中的关键物体、预测它们的运动和交互，以及规划安全的驾驶行为，来生成自然语言描述的驾驶指令。

## 不同视角

在 PDM-Lite 的实现中，DriveLM-Agent 作为其核心架构，利用视觉语言模型 BLIP-2 进行多阶段处理。首先，在感知阶段（P1），模型识别场景中的重要元素，如车辆、行人和交通信号灯；接着在预测阶段（P2），推断这些元素的未来运动；最后在规划阶段（P3），判断各种驾驶动作的安全性。这种分阶段的处理方式使得模型能够在复杂的驾驶环境中做出更为合理的决策。

## 共识与分歧

关于 PDM-Lite 的研究表明，采用图形视觉问答的方式能够有效提升自动驾驶系统的理解能力和决策质量。研究者们普遍认可其在处理复杂场景时的优势，尤其是在需要综合考虑多个因素的情况下。然而，关于其具体实现和性能评估的细节，仍存在一些分歧。例如，如何优化问答节点之间的依赖关系，以及如何在实际驾驶场景中验证其零样本泛化能力，都是当前研究中的挑战。

## 进一步阅读

对于希望深入了解 PDM-Lite 的读者，可以参考以下文献：
- "DriveLM: Driving with Graph Visual Question Answering" [[paper:14]]，该论文详细介绍了 PDM-Lite 的原理、架构和实验结果。
