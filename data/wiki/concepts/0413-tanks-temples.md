---
kind: "concept"
title: "Tanks & Temples"
aliases:
  - "concept:413"
  - "tanks-temples"
  - "Tanks & Temples"
concept_id: 413
slug: "tanks-temples"
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
compiled_at: "2026-05-13T11:54:08.848323+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "73e74aa671b861d41953b305307717cd298edbe2"
---

# Tanks & Temples

## 定义

Tanks & Temples 是一个用于评测 3D 重建与相机轨迹恢复能力的数据集/基准。在给定材料中，它被用作 LingBot-Map 的泛化评测之一，用来检验方法在不同场景尺度和类型下的重建稳定性与精度 [[paper:8]]。

## 在相关工作中的作用

在 [[paper:8]] 中，Tanks & Temples 不是论文的核心方法来源，而是作为实验基准出现。作者提出的 LingBot-Map 面向流式 3D 重建：模型在视频连续输入时，同时预测相机位姿与深度，并通过结构化几何上下文记忆减少长序列漂移。Tanks & Temples 与 ETH3D、7-Scenes 一起用于展示该方法并非只适用于单一室内或小尺度场景，而是在更广泛场景上也有一定泛化能力 [[paper:8]]。

具体结果上，LingBot-Map 在 Tanks & Temples 上取得 0.20 的 ATE；材料将其与 ETH3D 上的 0.22、7-Scenes 上的 0.08 并列，作为“从室内小场景到大尺度户外都具备较强泛化性”的证据 [[paper:8]]。

## 小结

基于当前材料，Tanks & Temples 可理解为流式 3D 重建方法的重要外部评测基准之一。它在 [[paper:8]] 中主要承担验证角色：检验 LingBot-Map 的结构化记忆机制是否能在不同场景条件下保持较好的轨迹精度与重建稳定性。
