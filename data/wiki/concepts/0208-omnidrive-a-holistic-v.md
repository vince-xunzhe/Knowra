---
kind: "concept"
title: "OmniDrive: A Holistic V…"
concept_id: 208
slug: "omnidrive-a-holistic-v"
node_type: "paper"
tags:
  - "Q-Former"
  - "开放环规划"
  - "反事实推理"
  - "自动驾驶"
  - "视觉语言模型"
  - "轨迹规划"
  - "nuScenes"
  - "反事实轨迹分析"
  - "OmniDrive"
  - "多视角图像"
  - "DriveLM"
  - "LLM-Agent"
  - "3D场景理解"
  - "Counterfactual Reasoning"
source_paper_ids:
  - 15
compiled_at: "2026-04-26T16:48:27.231227+00:00"
compile_model: "gpt-4o-mini"
---

# OmniDrive: A Holistic V…

# OmniDrive: A Holistic Vision-Language Dataset for Autonomous Driving with Counterfactual Reasoning

## 定义
OmniDrive 是一个面向自动驾驶的视觉语言数据与模型框架，旨在通过反事实推理提升自动驾驶系统的决策能力。与传统的驾驶数据集不同，OmniDrive 不仅记录专家司机的驾驶轨迹，还模拟多种可能的驾驶决策，例如“如果我左转/加速/直行会怎样”，并结合交通规则、3D物体、车道信息以及 GPT-4 生成的高质量问答，帮助模型理解不同决策的后果。

## 不同视角
在 OmniDrive 的架构中，数据流程首先从 nuScenes 多视角驾驶数据出发，利用 CLIP 提取前视图的语义特征，并通过 K-means 选择具有代表性的关键帧。系统随后按未来轨迹聚类，覆盖多种驾驶行为，如停车、直行、左转等。在每个场景中，OmniDrive 模拟多种候选轨迹，并通过规则检查潜在的碰撞、红灯和越界等问题，最终将结果与专家轨迹、3D物体、车道和多视角图像结合，交由 GPT-4 生成问答。

OmniDrive 提出了两种不同的智能体：Omni-L 和 Omni-Q。Omni-L 从 2D 视觉语言模型扩展而来，使用带有 3D 位置编码的 MLP 将图像特征投射到 LLM 的词向量空间；而 Omni-Q 则基于 3D 感知栈，通过查询结构进行感知查询和文本生成。这两种智能体在设计和功能上存在显著差异，反映了在自动驾驶领域中对视觉语言模型的不同应用方式。

## 共识与分歧
在当前的研究中，OmniDrive 的创新性和实用性得到了广泛认可。研究表明，OmniDrive 的预训练能够显著提升 DriveLM 的问答和 nuScenes 开放环规划表现，且从成熟的视觉语言模型迁移到 3D 感知的方式比传统的 3D 感知方法更为直接有效。然而，对于 Omni-L 和 Omni-Q 的具体优劣，仍存在一定的争议。部分研究者认为，Omni-Q 在处理复杂场景时表现更佳，而另一些研究者则指出 Omni-L 在特定条件下的效率更高。

## 进一步阅读
有关 OmniDrive 的详细信息和实验结果，可以参考论文《OmniDrive: A Holistic Vision-Language Dataset for Autonomous Driving with Counterfactual Reasoning》[[paper:15]]。该论文深入探讨了数据集的构建、模型架构以及实验评估，提供了对自动驾驶领域中视觉语言模型应用的全面理解。
