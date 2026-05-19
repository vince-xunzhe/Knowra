---
kind: "concept"
title: "Matterport3D"
aliases:
  - "concept:415"
  - "matterport3d"
  - "Matterport3D"
concept_id: 415
slug: "matterport3d"
node_type: "dataset"
concept_origin: "auto"
tags:
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
compiled_at: "2026-05-13T11:54:46.206114+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "757740de1b6341207905546566814c47f2dd5569"
---

# Matterport3D

## 定义

Matterport3D 在这里作为 **3D 重建模型训练所用的数据集** 出现，用于支持流式 3D 重建任务中的模型学习 [[paper:8]]。

在 [[paper:8]] 中，核心研究对象不是 Matterport3D 本身，而是 LingBot-Map：一个面向连续视频输入的实时 3D 重建模型。该方法通过结构化几何上下文记忆，在长视频中同时预测相机位姿与深度，目标是在实时性、轨迹稳定性和重建精度之间取得平衡 [[paper:8]]。

## 在该研究中的作用

Matterport3D 的角色主要是训练数据来源，而不是评测重点或方法创新点 [[paper:8]]。论文的主要贡献集中在模型结构上：将历史信息组织为 anchor 帧、局部 pose-reference window 和压缩的 trajectory memory，从而避免全量保存历史帧带来的计算和显存开销 [[paper:8]]。

因此，对该概念可以做一个较窄的理解：Matterport3D 在这篇工作中服务于室内/三维场景理解相关的训练过程，为模型学习几何、深度和相机运动关系提供数据基础 [[paper:8]]。

## 进一步阅读

若关注 Matterport3D 在该条目中的用途，应优先阅读 [[paper:8]] 的训练设置部分；若关注方法本身，则重点是 LingBot-Map 的 Geometric Context Attention、三类记忆设计，以及其在长序列重建中降低漂移和显存开销的实验结果。
