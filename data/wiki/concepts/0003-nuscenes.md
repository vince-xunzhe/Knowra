---
kind: "concept"
title: "nuScenes"
concept_id: 3
slug: "nuscenes"
node_type: "dataset"
tags:
  - "上下文查询"
  - "多视角图像"
  - "DriveLM"
  - "计划精度"
  - "空间推理"
  - "语义分割"
  - "Graph Visual Question Answering"
  - "指代表达分割"
  - "通用视觉模型"
  - "深度估计"
  - "3D边界框回归"
  - "语义关联"
  - "Vision Banana"
  - "实例分割"
  - "Vision-Language Model"
  - "对象查询"
  - "自动驾驶"
  - "3D场景理解"
  - "VLM"
  - "多视角3D视觉定位"
  - "End-to-end Autonomous Driving"
  - "轨迹规划"
  - "LoRA"
  - "Q-Former"
  - "nuScenes"
  - "坐标回归"
  - "反事实推理"
  - "LLM-Agent"
  - "视觉语言模型"
  - "Hierarchy of Grounding"
  - "CARLA"
  - "指令微调"
  - "OmniDrive"
  - "开放环规划"
  - "零样本迁移"
  - "Nano Banana Pro"
  - "NuScenes"
  - "单目度量深度"
  - "trajectory tokenization"
  - "DriveLM-Data"
  - "3D空间编码"
  - "多模态大语言模型"
  - "融合解码器"
  - "DriveLM-Agent"
  - "端到端驾驶"
  - "生成式视觉预训练"
  - "BEV特征"
  - "RGB可解码输出"
  - "graph prompting"
  - "表面法线估计"
source_paper_ids:
  - 2
  - 11
  - 12
  - 14
  - 15
compiled_at: "2026-04-26T16:21:56.832292+00:00"
compile_model: "gpt-4o-mini"
---

# nuScenes

# nuScenes

## 定义
nuScenes 是一个用于自动驾驶研究的数据集，提供了多视角的传感器数据和详细的标注信息。该数据集的设计旨在支持各种视觉和语言模型（VLM）在自动驾驶场景中的应用，促进对复杂交通环境的理解和决策能力的提升。

## 不同视角
在对 nuScenes 的研究中，多个论文提出了不同的应用和方法。例如，[[paper:2]] 提出的 SpaceDrive 利用统一的3D位置编码来增强 VLM 的空间推理能力，从而提升轨迹规划的精度。而 [[paper:12]] 的 NuGrounding 则结合了多视角图像和文本指令，通过融合视觉和语言信息来实现精确的3D物体定位。

此外，[[paper:14]] 的 DriveLM 通过图形化问答的方式，帮助模型理解和规划驾驶行为，强调了问题之间的依赖关系。相对而言，[[paper:15]] 的 OmniDrive 则引入了反事实推理，鼓励模型分析不同驾驶选择的后果，从而提升决策的安全性。

## 共识与分歧
在对 nuScenes 数据集的应用研究中，学者们普遍认可其在自动驾驶领域的重要性，尤其是在训练和评估视觉语言模型方面。然而，对于如何最有效地利用这些数据，存在一些分歧。例如，虽然有研究强调了多模态融合的重要性，但在具体的实现方法和模型架构上，各篇论文提出了不同的策略和技术细节。

此外，尽管大多数研究集中在提升模型的空间理解和决策能力上，但对反事实推理的重视程度在不同研究中有所不同，这反映了对自动驾驶系统安全性和可靠性的不同看法。

## 进一步阅读
- 论文 [[paper:2]] 提出了 SpaceDrive 方法，强调了空间推理能力的提升。
- 论文 [[paper:12]] 介绍了 NuGrounding 框架，融合了多视角和文本信息以实现3D定位。
- 论文 [[paper:14]] 讨论了 DriveLM 的图形化问答机制，强调了问题依赖性。
- 论文 [[paper:15]] 探讨了 OmniDrive 数据集，关注反事实推理在自动驾驶中的应用。
