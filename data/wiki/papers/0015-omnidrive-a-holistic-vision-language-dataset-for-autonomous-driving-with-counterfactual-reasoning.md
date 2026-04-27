---
kind: "paper"
title: "OmniDrive: A Holistic Vision-Language Dataset for Autonomous Driving with Counterfactual Reasoning"
paper_id: 15
slug: "omnidrive-a-holistic-vision-language-dataset-for-autonomous-driving-with-counterfactual-reasoning"
authors:
  - "Shihao Wang"
  - "Zhiding Yu"
  - "Xiaohui Jiang"
  - "Shiyi Lan"
  - "Min Shi"
  - "Nadine Chang"
  - "Jan Kautz"
  - "Ying Li"
  - "Jose M. Alvarez"
compiled_at: "2026-04-26T16:18:52.707703+00:00"
compile_model: "gpt-4o-mini"
source_record: "data/paper_records/0015-2405.01533v2.md"
---

# OmniDrive: A Holistic Vision-Language Dataset for Autonomous Driving with Counterfactual Reasoning

## 一句话定位
《OmniDrive》是一项针对自动驾驶的视觉语言数据集与模型框架，旨在通过反事实推理提升模型对3D交通的理解和可解释规划能力。

## 核心贡献
本论文的核心贡献在于将自动驾驶轨迹、3D场景和语言问答结合起来，使得模型不仅能够驾驶，还能解释其决策过程。通过引入反事实轨迹，OmniDrive 提供了更丰富的监督信号，帮助模型理解不同驾驶选择的后果。

## 方法
论文提出的 OmniDrive 框架包括以下几个关键步骤：
1. **数据流程**：从 [[nuScenes]] 多视角驾驶数据出发，利用 [[CLIP]] 提取前视图语义特征，并通过 [[K-means]] 选择代表性关键帧。
2. **轨迹模拟**：根据未来轨迹聚类，覆盖多种驾驶行为（如停车、直行、左转等），并通过规则检查潜在的碰撞、红灯和越界问题。
3. **问答生成**：将专家轨迹、3D物体、车道、地图元素和多视角图像输入 [[GPT-4]] 生成高质量问答。
4. **模型架构**：Omni-L 和 Omni-Q 两种模型分别基于不同的视觉语言模型架构进行设计，前者使用带3D位置编码的 [[MLP]] 进行视觉语言对齐，后者则利用 [[Q-Former]] 进行查询聚合。

## 实验与结论
实验结果表明，OmniDrive 的预训练显著提升了 [[DriveLM]] 的问答和 [[nuScenes]] 开放环规划表现。具体而言，Omni-L 在 DriveLM 上的总分从0.53提升至0.56，加入 [[LLaVA665K]] 后达到0.58。在开放环规划中，Omni-L 的碰撞率和越界率均优于 Omni-Q，反事实推理的精度和召回率也显示出明显优势。

## 限制与待解
OmniDrive 的主要限制在于反事实模拟仍偏向开放环，未能充分考虑其他交通参与者的反应。此外，数据生成过程依赖于 [[GPT-4]]、规则清单和人工质检，成本较高且可能引入语言模型偏见。开放环指标可能受到自车状态和数据分布的影响，不能完全代表真实的上路安全。

## 涉及概念
- [[OmniDrive]]
- [[自动驾驶]]
- [[视觉语言模型]]
- [[反事实推理]]
- [[3D场景理解]]
- [[轨迹规划]]
- [[nuScenes]]
- [[DriveLM]]
- [[Q-Former]]
- [[多视角图像]]
- [[开放环规划]]
