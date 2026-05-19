---
kind: "concept"
title: "UniAD"
aliases:
  - "concept:90"
  - "uniad"
  - "UniAD"
concept_id: 90
slug: "uniad"
node_type: "technique"
concept_origin: "auto"
tags:
  - "Graph Visual Question Answering"
  - "端到端规划"
  - "自动驾驶"
  - "驾驶行为预测"
  - "因果注意力"
  - "BLIP-2"
  - "多步推理"
  - "轨迹预测"
  - "Vision-Language-Action"
  - "LoRA"
  - "3D目标检测"
  - "Vision Language Model"
  - "多视角深度估计"
  - "端到端自动驾驶"
  - "多任务学习"
  - "空间感知"
  - "nuScenes"
  - "零样本泛化"
  - "轨迹离散化"
  - "Bench2Drive"
  - "坐标回归"
  - "车道线检测"
  - "统一解码器"
  - "Vision-Language Model"
  - "3D位置编码"
  - "视觉语言模型"
  - "轨迹规划"
source_paper_ids:
  - 2
  - 24
  - 14
compiled_at: "2026-05-13T10:04:49.117619+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "d20fee7ff8fdcd105f115f210bf6a1307bb3b523"
---

# UniAD

## 定义

在给定材料中，**UniAD** 主要作为自动驾驶研究中的一个 **baseline / 参照方法** 出现，而不是被详细展开的核心方法。因此，这里只能把它理解为：用于衡量新一代 VLM/VLA 自动驾驶方法在感知、推理、规划等能力上改进幅度的传统或既有端到端驾驶基线。

这些论文的重点并不是重新定义 UniAD，而是通过与类似 UniAD 这类驾驶专用系统或传统端到端框架的比较，说明视觉语言模型路线在自动驾驶中的新趋势：从单纯轨迹预测，走向带语言监督、图式推理、空间编码和统一多任务解码的驾驶智能。

## 不同视角

一类工作把 UniAD 所代表的基线视为“驾驶专用端到端系统”的参照对象。DriveLM 关注的是如何让通用视觉语言模型通过 Graph VQA 分阶段完成感知、预测、规划、行为生成和轨迹输出，从而在开放环规划上接近甚至超过部分驾驶专用方法 [[paper:14]]。在这个视角下，UniAD 这类 baseline 的作用是提供一个强驾驶系统参照，用来检验语言化、可解释推理是否真的能转化为驾驶性能。

另一类工作关注 VLM 自动驾驶相对于传统端到端方法的空间短板。SpaceDrive 指出现有 VLM-based driving 方法虽然具备图像理解和语言推理能力，但三维空间关系和精确轨迹数值生成较弱；它通过统一 3D 位置编码，把视觉 token、文本坐标和轨迹回归连接到同一空间表示中 [[paper:2]]。从这个角度看，UniAD 类 baseline 隐含代表了驾驶任务中对空间几何和轨迹精度的硬要求。

OneDrive 则从系统结构角度推进：它把图像 token、检测查询、车道查询、规划查询和文本 token 放进同一个 Transformer 解码器，用共享注意力骨干统一处理感知、规划和文本生成 [[paper:24]]。这说明新方法不只是追求超过某个 baseline 指标，也在尝试减少多头、多模块系统的复杂性。

## 共识与分歧

这些论文的共同点是：自动驾驶不能只依赖“看图后直接输出轨迹”。DriveLM 强调中间行为和图结构问答的重要性 [[paper:14]]；SpaceDrive 强调显式三维空间编码对规划精度的作用 [[paper:2]]；OneDrive 强调感知、规划、文本生成可以共享同一个注意力骨干 [[paper:24]]。它们都把 UniAD 这类 baseline 放在一个背景中：传统强基线有效，但新问题在于如何让模型更可解释、更统一、更具空间感。

分歧主要在于“应该补什么能力”。DriveLM 认为关键是把驾驶推理拆成有逻辑依赖的问答图，让模型先解释再行动 [[paper:14]]。SpaceDrive 认为 VLM 的主要瓶颈是三维坐标与视觉语义没有可靠对齐，因此要改造坐标表示和轨迹输出方式 [[paper:2]]。OneDrive 则认为架构碎片化是问题，主张用单一解码器整合结构化任务和文本任务 [[paper:24]]。

## 未解问题

材料没有直接说明 UniAD 的内部架构、训练目标或具体指标，因此不能判断这些新方法相对于 UniAD 的完整优劣关系。现有片段只能支持一个有限结论：UniAD 在这些工作中更像是自动驾驶端到端 baseline 的代表，而不是被讨论的核心技术对象。

更大的未解问题是：VLM/VLA 驾驶方法在开环指标、闭环仿真、真实部署安全性之间是否能稳定一致。SpaceDrive 和 OneDrive 都报告了较强的规划或闭环结果 [[paper:2]][[paper:24]]，DriveLM 也展示了可解释推理和零样本泛化优势 [[paper:14]]，但材料没有给出它们与 UniAD 在统一实验设置下的全面对照。
