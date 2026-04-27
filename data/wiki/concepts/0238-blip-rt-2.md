---
kind: "concept"
title: "BLIP-RT-2"
concept_id: 238
slug: "blip-rt-2"
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
compiled_at: "2026-04-26T16:54:05.880656+00:00"
compile_model: "gpt-4o-mini"
---

# BLIP-RT-2

## 定义

BLIP-RT-2 是一种基于视觉语言模型的技术，主要应用于自动驾驶领域，特别是在图像理解和问答系统中。它的核心理念是通过将驾驶场景中的关键问题和答案组织成一个有向无环图（GVQA），以此来辅助驾驶决策。该模型不仅关注图像的直接分析，还强调了问题之间的依赖关系，从而形成一个系统化的决策过程。

## 不同视角

在 DriveLM 的框架下，BLIP-RT-2 作为视觉语言模型的骨架，分为三个主要阶段：感知、预测和规划。在感知阶段，模型识别场景中的关键物体和交通标志；在预测阶段，推断这些物体的运动和相互作用；最后在规划阶段，判断安全与危险的驾驶动作。这种结构化的问答流程使得模型能够更有效地生成自然语言描述的驾驶行为。

## 共识与分歧

关于 BLIP-RT-2 的应用，研究者们普遍认可其在自动驾驶中的潜力，尤其是在复杂场景下的决策支持能力。然而，仍存在一些未解的问题，例如如何进一步提高模型在动态环境中的适应性和实时性。此外，尽管 DriveLM 提出了新的驾驶评测指标，但如何量化这些指标的有效性仍需进一步探讨。

## 进一步阅读

欲了解更多关于 BLIP-RT-2 的信息，可以参考以下文献：
- "DriveLM: Driving with Graph Visual Question Answering" [[paper:14]]。
