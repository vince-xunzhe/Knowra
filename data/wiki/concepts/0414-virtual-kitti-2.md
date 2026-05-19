---
kind: "concept"
title: "Virtual KITTI 2"
aliases:
  - "concept:414"
  - "virtual-kitti-2"
  - "Virtual KITTI 2"
concept_id: 414
slug: "virtual-kitti-2"
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
compiled_at: "2026-05-13T11:54:27.563166+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "2eb664e5a6bd075e9128b230634f064e39bb22c2"
---

# Virtual KITTI 2

## 定义

Virtual KITTI 2 在当前材料中被标注为一个训练用数据集，用于 3D 重建/相机轨迹估计相关模型的训练语境。现有片段只说明它与 LingBot-Map 的训练有关，并未提供该数据集的具体组成、场景类型、标注形式或规模信息，因此不应进一步推断其数据内容 [[paper:8]]。

## 在论文中的作用

在 “Geometric Context Transformer for Streaming 3D Reconstruction” 中，Virtual KITTI 2 出现在训练数据集语境下。该论文的核心关注点不是数据集本身，而是提出 LingBot-Map：一种面向流式视频 3D 重建的方法，通过锚点、局部窗口和轨迹记忆三类几何上下文，在实时处理连续视频时同时预测相机位姿与深度，并抑制长程漂移 [[paper:8]]。

## 可确定信息与限制

从现有材料能确定的是：Virtual KITTI 2 被用作训练相关数据集之一；它服务于视频输入下的几何重建任务，而不是作为论文的主要方法贡献。材料没有给出该数据集上的单独实验结果、消融结论或与其他训练集的对比，因此无法总结它对模型性能的独立影响 [[paper:8]]。
