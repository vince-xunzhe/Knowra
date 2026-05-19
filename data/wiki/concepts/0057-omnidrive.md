---
kind: "concept"
title: "OmniDrive"
aliases:
  - "concept:57"
  - "omnidrive"
  - "OmniDrive"
concept_id: 57
slug: "omnidrive"
node_type: "technique"
concept_origin: "auto"
tags:
  - "端到端规划"
  - "自动驾驶"
  - "因果注意力"
  - "多视角3D视觉指代"
  - "NuGrounding"
  - "Vision-Language-Action"
  - "LoRA"
  - "3D目标检测"
  - "视觉定位"
  - "融合解码器"
  - "多视角深度估计"
  - "上下文查询"
  - "端到端自动驾驶"
  - "多任务学习"
  - "空间感知"
  - "nuScenes"
  - "Bench2Drive"
  - "坐标回归"
  - "多模态大语言模型"
  - "车道线检测"
  - "HoG"
  - "BEV"
  - "统一解码器"
  - "Vision-Language Model"
  - "3D位置编码"
  - "视觉语言模型"
  - "轨迹规划"
source_paper_ids:
  - 12
  - 2
  - 24
compiled_at: "2026-05-13T10:02:59.592348+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "24d8d55bedd507c394cc8eabf71fef76cd68cd0d"
---

# OmniDrive

## 定义

OmniDrive 在这些材料中主要作为 VLM-based 自动驾驶方法的基础模型或 baseline 出现，而不是被单独展开为一个新方法。它代表了一类“会看图、会用语言模型推理并输出驾驶决策”的视觉语言自动驾驶框架，但其关键短板是三维空间建模不够精细：模型能处理视觉语义和文本推理，却不擅长把坐标、目标位置和轨迹数字稳定对齐 [[paper:2]]。

## 不同视角

从 SpaceDrive 的视角看，OmniDrive 的问题在于空间信息仍然更像文本或隐式视觉特征，而不是统一坐标系中的可计算表示。SpaceDrive 以 OmniDrive 为基础对照，证明加入统一 3D 位置编码后，即使不额外加入 ego 状态，也能让平均 L2 改善 0.18、碰撞率下降 1.91%，说明 OmniDrive 的瓶颈至少部分来自空间感知不足 [[paper:2]]。

从 NuGrounding 的视角看，类似 OmniDrive 这类依赖多模态大模型的方案若要处理自动驾驶中的实例级 3D grounding，仅靠语言理解和通用视觉特征并不够。NuGrounding 强调需要 BEV 检测器产生带几何先验的 object query，再让大模型理解语言并融合语义与空间信息 [[paper:12]]。这与 SpaceDrive 对 OmniDrive 的批评方向一致：自动驾驶 VLM 需要更显式、更可靠的三维结构接口。

从 OneDrive 的视角看，问题不只在空间编码，也在任务组织方式。OneDrive 试图把图像 token、检测/车道/规划查询 token 和文本 token 放入同一个 Transformer 解码器，用共享注意力同时处理感知、规划和文本生成 [[paper:24]]。它没有直接把 OmniDrive 作为核心讨论对象，但提供了另一条改进 VLM 驾驶系统的路线：不是单独修补坐标表达，而是重构多任务解码范式。

## 共识与分歧

这些论文形成的共识是：以 OmniDrive 为代表的 VLM 驾驶 baseline 已经具备多模态理解和推理能力，但若要胜任自动驾驶，必须补足结构化空间能力。SpaceDrive 用统一 3D 位置编码解决坐标与视觉 token 对齐问题 [[paper:2]]；NuGrounding 用 object query 和融合解码器解决语言指代到 3D 实例的定位问题 [[paper:12]]；OneDrive 则通过统一解码器让感知、规划和文本任务共享预训练注意力能力 [[paper:24]]。

分歧主要在“该把空间和任务结构放在哪里”。SpaceDrive 倾向于把三维坐标显式编码进视觉和文本 token，使 VLM 自身具备空间感 [[paper:2]]；NuGrounding 更依赖专业 3D 检测器提供几何先验，再让大模型处理语言语义 [[paper:12]]；OneDrive 则强调统一 token 序列和共享解码器，减少多解码器系统中的割裂 [[paper:24]]。

## 未解问题

材料中没有给出 OmniDrive 的完整架构细节，因此无法判断它具体在哪些模块上失败：是视觉 token 缺少深度，文本坐标表达不稳定，还是多任务解码方式限制了规划能力。现有证据只能支持一个较窄结论：作为 baseline，OmniDrive 在三维空间理解和精确轨迹规划上弱于显式空间增强后的 SpaceDrive [[paper:2]]。
