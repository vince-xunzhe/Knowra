---
kind: "concept"
title: "DriveLM"
concept_id: 6
slug: "drivelm"
node_type: "technique"
tags:
  - "开放环规划"
  - "反事实推理"
  - "自动驾驶"
  - "语义关联"
  - "nuScenes"
  - "OmniDrive"
  - "VLM"
  - "坐标回归"
  - "轨迹规划"
  - "计划精度"
  - "DriveLM"
  - "LLM-Agent"
  - "端到端驾驶"
  - "深度估计"
  - "空间推理"
  - "Q-Former"
  - "视觉语言模型"
  - "多视角图像"
  - "3D场景理解"
  - "3D空间编码"
source_paper_ids:
  - 2
  - 15
compiled_at: "2026-04-26T16:22:19.690021+00:00"
compile_model: "gpt-4o-mini"
---

# DriveLM

# DriveLM

## 定义
DriveLM是一个专注于驾驶视觉问答的评测与微调的数据集，旨在提升自动驾驶系统在复杂环境中的理解和决策能力。该概念涉及通过视觉和语言模型的结合，增强自动驾驶系统的空间推理和决策能力。

## 不同视角
在DriveLM的研究中，两个主要的论文提供了不同的视角和方法来实现这一目标。

### SpaceDrive
论文《SpaceDrive: Infusing Spatial Awareness into VLM-based Autonomous Driving》提出了一种将空间意识融入视觉语言模型的方法。其核心思想是通过视觉编码器和深度估算器，将环境图像转化为3D位置编码，从而提升模型在轨迹规划中的精度。该方法强调了视觉特征和语言特征的对齐，利用统一的3D位置编码来增强空间推理能力[[paper:2]]。

### OmniDrive
另一篇论文《OmniDrive: A Holistic Vision-Language Dataset for Autonomous Driving with Counterfactual Reasoning》则引入了反事实推理的概念，模拟新手司机在不同情境下的决策过程。该研究通过构建一个包含多种驾驶行为的全面数据集，帮助模型理解不同选择的后果。OmniDrive的数据流程涉及从多视角驾驶数据中提取语义特征，并利用GPT-4生成问答，以提升模型的问答和规划能力[[paper:15]]。

## 共识与分歧
在DriveLM的研究中，学者们普遍同意将视觉和语言模型结合是提升自动驾驶系统智能化的有效途径。两篇论文均强调了空间推理的重要性，并探索了不同的编码和推理机制。然而，关于如何实现这一目标的具体方法存在分歧。SpaceDrive更侧重于3D位置编码的应用，而OmniDrive则关注于反事实推理的引入，展示了不同的研究方向和技术路径。

## 进一步阅读
- [SpaceDrive: Infusing Spatial Awareness into VLM-based Autonomous Driving](https://example.com/paper2)
- [OmniDrive: A Holistic Vision-Language Dataset for Autonomous Driving with Counterfactual Reasoning](https://example.com/paper15)
