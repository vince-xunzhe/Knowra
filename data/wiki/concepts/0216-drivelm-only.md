---
kind: "concept"
title: "DriveLM-only"
concept_id: 216
slug: "drivelm-only"
node_type: "technique"
tags:
  - "Q-Former"
  - "开放环规划"
  - "反事实推理"
  - "自动驾驶"
  - "视觉语言模型"
  - "轨迹规划"
  - "nuScenes"
  - "3D场景理解"
  - "OmniDrive"
  - "多视角图像"
  - "DriveLM"
  - "LLM-Agent"
source_paper_ids:
  - 15
compiled_at: "2026-04-26T16:49:43.026938+00:00"
compile_model: "gpt-4o-mini"
---

# DriveLM-only

## DriveLM-only

DriveLM-only 是一种基线技术，主要应用于自动驾驶领域，旨在通过结合视觉和语言的理解来提升模型的决策能力。该技术的核心在于通过反事实推理来训练模型，使其不仅能够识别和模仿驾驶行为，还能分析不同选择的后果。

## 定义

DriveLM-only 通过利用 OmniDrive 数据集，采用了一种新颖的训练方法。该方法模拟新手司机的学习过程，不仅让模型了解正确的驾驶轨迹，还引导其思考如果选择其他轨迹可能造成的后果。例如，模型会被问到“如果你现在加速左转会怎样？”这样的问题，从而促使其理解交通信号、车道、行人和障碍物的影响。

## 不同视角

在 DriveLM-only 的实现中，数据流程首先从 nuScenes 多视角驾驶数据出发，使用 CLIP 提取前视图的语义特征。接着，通过 K-means 选择语义上具有代表性的关键帧，并根据未来轨迹进行聚类，涵盖多种驾驶行为，如停车、直行、左转等。系统会模拟多种候选轨迹，并通过规则检查潜在的碰撞、红灯和越界等问题。

## 共识与分歧

在对 DriveLM-only 的研究中，学者们普遍认可其通过反事实问答提升模型决策能力的潜力[[paper:15]]。然而，对于如何进一步优化模型的结构和训练流程，仍存在不同看法。例如，Omni-L 和 Omni-Q 两种架构的比较显示了不同的特征提取和查询机制对模型性能的影响，但尚未达成一致的最佳实践。

## 进一步阅读

欲深入了解 DriveLM-only 的实现和应用，可以参考论文《OmniDrive: A Holistic Vision-Language Dataset for Autonomous Driving with Counterfactual Reasoning》，该文详细介绍了数据集的构建、模型架构以及实验结果。
