---
kind: "concept"
title: "Geometric Context Atten…"
aliases:
  - "concept:408"
  - "geometric-context-atten"
  - "Geometric Context Atten…"
concept_id: 408
slug: "geometric-context-atten"
node_type: "technique"
concept_origin: "auto"
tags:
  - "几何上下文注意力"
  - "GCA"
  - "Trajectory Memory"
  - "Geometric Context Attention"
  - "Depth Prediction"
  - "Streaming 3D Reconstruction"
  - "Camera Pose Estimation"
  - "SLAM"
  - "Video RoPE"
  - "Vision Transformer"
  - "Visual Geometry"
  - "Geometric Context Transformer"
source_paper_ids:
  - 8
compiled_at: "2026-05-13T11:53:25.086159+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "5884d057f38389bc97e2b237be4d57921e30aeec"
---

# Geometric Context Atten…

## 定义

Geometric Context Attention（GCA，跨帧几何聚合）是 LingBot-Map 中用于流式 3D 重建的结构化跨帧注意力模块。它的核心目标是在视频连续输入时，让当前帧能够读取必要的历史几何信息，同时避免把所有历史帧完整保留下来导致计算和显存持续增长 [[paper:8]]。

GCA 不做简单的全历史注意力，而是把历史上下文拆成三类记忆：最早的 anchor 帧用于固定坐标系和尺度；最近若干帧组成 pose-reference window，保留完整图像 token，用于近邻精细配准；更早的历史则压缩成 trajectory memory，只保留少量上下文 token，并加入时间位置编码来维持轨迹顺序信息 [[paper:8]]。

## 作用机制

在 LingBot-Map 的整体流程中，每帧先经过以 DINOv2 初始化的 ViT 主干提取图像 token，并拼接 camera token、register token 和 anchor token。随后网络交替使用 Frame Attention 和 Geometric Context Attention：前者整理单帧内部特征，后者跨帧聚合几何上下文，使当前帧的位姿和深度预测能够同时参考局部邻近帧、全局锚点和长期轨迹记忆 [[paper:8]]。

这种设计相当于在“记住足够多”和“实时运行”之间做了结构化折中：局部窗口负责短期精确对齐，anchor 提供全局参照，trajectory memory 抑制长程漂移。论文报告显示，相比全因果注意力，有界 pose-reference window 不仅降低显存和提升 FPS，还能改善 ATE；加入 Video RoPE 后 ATE 也明显下降，说明时间顺序对轨迹记忆有效性很关键 [[paper:8]]。

## 价值

GCA 的主要价值在于把长视频中的历史信息组织成可持续更新的几何记忆，而不是把所有帧无差别堆入注意力。这样 LingBot-Map 能在流式场景下同时兼顾实时性、轨迹稳定性和重建精度 [[paper:8]]。

从实验结果看，这种跨帧几何聚合有助于降低长序列漂移，并在室内、小场景和大尺度户外数据集上表现出一定泛化能力 [[paper:8]]。因此，GCA 可以被理解为一种面向流式 3D 重建的“长期但有界”的注意力记忆机制。
