---
kind: "concept"
title: "NuScenes"
aliases:
  - "concept:53"
  - "nuscenes"
  - "NuScenes"
concept_id: 53
slug: "nuscenes"
node_type: "dataset"
concept_origin: "auto"
tags:
  - "表面法线估计"
  - "指代表达分割"
  - "Graph Visual Question Answering"
  - "端到端规划"
  - "自动驾驶"
  - "Q-Former"
  - "指令微调"
  - "驾驶行为预测"
  - "因果注意力"
  - "开放环规划"
  - "DriveLM"
  - "BLIP-2"
  - "多步推理"
  - "多视角3D视觉指代"
  - "单目度量深度估计"
  - "通用视觉学习"
  - "NuGrounding"
  - "Vision-Language-Action"
  - "LoRA"
  - "轨迹预测"
  - "3D目标检测"
  - "反事实推理"
  - "视觉定位"
  - "统一视觉接口"
  - "图像生成预训练"
  - "融合解码器"
  - "Vision Language Model"
  - "多视角深度估计"
  - "3D场景理解"
  - "上下文查询"
  - "端到端自动驾驶"
  - "多任务学习"
  - "空间感知"
  - "nuScenes"
  - "零样本泛化"
  - "轨迹离散化"
  - "Bench2Drive"
  - "坐标回归"
  - "多模态大语言模型"
  - "实例分割"
  - "LLM-agent"
  - "车道线检测"
  - "HoG"
  - "RGB可解码可视化"
  - "BEV"
  - "统一解码器"
  - "多视角图像"
  - "Vision-Language Model"
  - "3D位置编码"
  - "语义分割"
  - "视觉语言模型"
  - "轨迹规划"
source_paper_ids:
  - 12
  - 15
  - 2
  - 24
  - 11
  - 14
compiled_at: "2026-05-13T10:02:28.908944+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "9a321f59cf3827ee8278b67bf0d19f4f9bf58e02"
---

# NuScenes

## 定义

NuScenes 在这些材料中主要作为自动驾驶研究的底层数据来源与评测基准出现：它提供环视多相机驾驶场景、3D目标、地图/车道、未来轨迹等信息，使研究者可以在真实道路场景上构造视觉语言、3D grounding、规划和推理任务。

从使用方式看，NuScenes 不只是一个“感知数据集”，而逐渐成为多模态自动驾驶数据集的母体：NuGrounding 基于 nuScenes 构建多视角 3D 视觉指代数据，用语言描述去定位 3D目标 [[paper:12]]；OmniDrive 从 nuScenes 场景中生成反事实驾驶问答，用来训练模型理解“如果这样开会怎样” [[paper:15]]；DriveLM 将 nuScenes 场景改造成图结构 VQA 数据，覆盖感知、预测、规划、行为和运动链路 [[paper:14]]。

## 不同视角

一种视角把 NuScenes 看作多视角 3D 感知与定位的基础。NuGrounding 使用车辆周围六个相机图像和 3D 检测器生成对象查询，再把语言指令与实例级几何信息融合，用于自动驾驶场景中的 3D visual grounding [[paper:12]]。在这里，NuScenes 的价值在于提供多视角图像和可用于恢复空间结构的 3D 场景基础。

另一种视角把 NuScenes 看作规划与驾驶推理的评测平台。SpaceDrive 在 nuScenes 开环评测中验证 VLM 的空间位置编码能降低轨迹 L2、碰撞率和越界率 [[paper:2]]；OneDrive 也在 nuScenes 开环规划上报告平均 L2 和碰撞率，用它衡量统一视觉-语言-动作模型的规划能力 [[paper:24]]。这说明 NuScenes 已被用来检验模型是否不仅能“看懂场景”，还能生成可行驶轨迹。

第三种视角把 NuScenes 看作语言监督再构造的原始素材。OmniDrive 从 nuScenes 中选择代表性关键帧和未来轨迹，结合模拟轨迹、规则检查和 GPT-4 生成场景描述、注意对象与反事实问答 [[paper:15]]。DriveLM 则把 nuScenes 帧组织成图结构问答，每帧包含大量围绕驾驶决策的 QA 监督 [[paper:14]]。这类工作共同说明，NuScenes 的原始传感器与轨迹标注可以被“编译”为更密集的语言推理数据。

## 共识与分歧

这些论文的共识是：NuScenes 提供了足够丰富的真实驾驶场景，使它能支撑从 3D检测、视觉 grounding、问答推理到轨迹规划的一整条研究链路。多篇工作都在 nuScenes 上报告规划或定位结果，并把它作为验证自动驾驶多模态模型的重要基准 [[paper:2]][[paper:12]][[paper:24]]。

另一个共识是，仅有图像或轨迹监督并不足够。DriveLM 认为需要把感知、预测、规划拆成图结构问答，才能让 VLM 更可解释地做驾驶决策 [[paper:14]]；OmniDrive 强调反事实后果监督，例如碰撞、闯红灯、越界，能比单纯专家轨迹提供更密集的学习信号 [[paper:15]]；SpaceDrive 则指出 VLM 还需要显式三维位置编码，否则难以把坐标和图像目标准确对齐 [[paper:2]]。

分歧主要在于如何把 NuScenes 的空间信息接入语言模型。NuGrounding 倾向于借助专业 3D 检测器生成带几何先验的 object query，再让多模态大模型理解语言 [[paper:12]]；SpaceDrive 尝试不用稠密 BEV 特征，而是把 3D 坐标统一编码进视觉 token 和文本坐标 token [[paper:2]]；OmniDrive 的实验则显示，从强 2D VLM 扩展到 3D驾驶任务的路线，比把传统 3D感知表征接到语言模型上更直接有效 [[paper:15]]。OneDrive 进一步提出把检测、车道、规划和文本 token 放进同一个解码器，以共享预训练 VLM 的注意力能力 [[paper:24]]。

## 未解问题

这些材料也暴露出 NuScenes 作为基础数据源的局限：研究者普遍需要在其上二次构造语言、反事实、图结构或 grounding 标注，说明原始数据本身并不直接满足 VLM 驾驶推理训练的需求 [[paper:12]][[paper:14]][[paper:15]]。

此外，NuScenes 开环指标虽然能衡量轨迹误差和碰撞率，但多篇工作仍结合其他闭环或泛化评测，例如 Bench2Drive、NAVSIM、Waymo 零样本测试，说明仅依赖 nuScenes 还不足以完整评估真实驾驶能力 [[paper:2]][[paper:14]][[paper:24]]。

## 进一步阅读

想了解 NuScenes 如何被扩展成语言 grounding 数据，可读 NuGrounding [[paper:12]]。  
想了解反事实驾驶问答和规划监督，可读 OmniDrive [[paper:15]]。  
想了解图结构 VQA 驾驶推理，可读 DriveLM [[paper:14]]。  
想了解 NuScenes 上的 VLM 规划与统一解码框架，可读 SpaceDrive 和 OneDrive [[paper:2]][[paper:24]]。
