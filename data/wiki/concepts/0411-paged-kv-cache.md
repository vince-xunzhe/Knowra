---
kind: "concept"
title: "Paged KV-Cache"
aliases:
  - "concept:411"
  - "paged-kv-cache"
  - "Paged KV-Cache"
concept_id: 411
slug: "paged-kv-cache"
node_type: "technique"
concept_origin: "auto"
tags:
  - "Trajectory Memory"
  - "Geometric Context Attention"
  - "Depth Prediction"
  - "Streaming 3D Reconstruction"
  - "分页KV缓存"
  - "Paged Attention"
  - "Camera Pose Estimation"
  - "SLAM"
  - "Video RoPE"
  - "Vision Transformer"
  - "Visual Geometry"
  - "Geometric Context Transformer"
source_paper_ids:
  - 8
compiled_at: "2026-05-13T11:53:49.286011+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "5e8fb733e1f4263d1cdba72d61b9af7f3cc9783a"
---

# Paged KV-Cache

## 定义

Paged KV-Cache 在这组材料中不是传统语言模型推理里的通用 KV-cache 分页机制，而是指一种面向长视频流式推理的“分页式上下文记忆”思想：不把所有历史帧完整保存在注意力上下文中，而是把历史信息拆成不同用途、不同粒度的记忆块，以控制显存和计算量，同时保留长期几何一致性。

在 LingBot-Map 中，这种思想体现在 Geometric Context Attention：历史上下文被分为三类。第一类是 anchor 帧，用来固定全局坐标系和尺度；第二类是最近若干帧的 pose-reference window，保留完整图像 token，用于近邻配准；第三类是更久远历史压缩后的 trajectory memory，只保留少量上下文 token，并结合时间位置编码表示长期轨迹信息 [[paper:8]]。

## 核心作用

这种机制的目的，是在流式 3D 重建中避免两种极端：一是完整保留所有历史帧，导致注意力计算和显存随视频长度快速增长；二是过度丢弃历史，导致相机轨迹长期漂移。Paged KV-Cache 式的结构化记忆让模型把“当前需要精细对齐的局部信息”和“长期防漂移的全局轨迹信息”分开存放、分开读取 [[paper:8]]。

实验上，有界 pose-reference window 代替全因果注意力后，模型速度从 11.87 FPS 提升到 20.29 FPS，显存从 36.06 GB 降到 13.28 GB，同时 ATE 还从 6.60 降到 5.98，说明这种记忆组织并不只是节省资源，也能改善轨迹估计质量 [[paper:8]]。

## 关键设计

该方法的关键不是简单“缓存更多”或“缓存更少”，而是按功能分层缓存：

- anchor memory：提供稳定的坐标与尺度参照。
- local window：保存最近帧的完整 token，服务于短期精细匹配。
- trajectory memory：压缩更久远历史，只保留轨迹相关上下文，服务于长期一致性。

其中，时间顺序编码很重要。论文中加入 Video RoPE 后，ATE 从 7.46 降到 5.98，说明压缩后的长期记忆如果没有可靠的时间位置信息，就难以有效支持轨迹推理 [[paper:8]]。

## 小结

在这篇论文语境下，Paged KV-Cache 可以理解为一种高效流式推理的结构化记忆方案：用有限、分层、按需读取的上下文替代无限增长的全历史注意力。它的价值在于同时降低显存、提高速度，并缓解长视频 3D 重建中的累计漂移问题 [[paper:8]]。
