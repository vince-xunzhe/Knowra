---
kind: "concept"
title: "Aria Digital Twin"
aliases:
  - "concept:417"
  - "aria-digital-twin"
  - "Aria Digital Twin"
concept_id: 417
slug: "aria-digital-twin"
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
compiled_at: "2026-05-13T11:55:24.806328+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "5cd7c78c0361d2ceb54f8f0b80af4475cff9e7f9"
---

# Aria Digital Twin

## 定义

Aria Digital Twin 在当前材料中作为一个训练数据集出现，用于支持流式 3D 重建相关模型的训练或实验背景 [[paper:8]]。

## 在论文中的作用

在 [[paper:8]] 中，核心研究对象是 LingBot-Map：一种面向连续视频输入的实时 3D 重建方法。论文关注如何在长视频流中同时保持相机轨迹稳定、深度预测准确和计算开销可控。Aria Digital Twin 在材料中仅被标注为“训练”数据集，但未提供其具体规模、采集方式、标注类型或与其他数据集的对比信息。

## 可确认的信息边界

基于现有材料，可以确认的是：Aria Digital Twin 与流式 3D 重建任务相关，并在 [[paper:8]] 的训练语境中被涉及。材料没有说明它是否包含 RGB 视频、深度、相机位姿、3D 场景模型，或是否专门用于 egocentric / wearable camera 场景，因此这些细节不能进一步展开。
