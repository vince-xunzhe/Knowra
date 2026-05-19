---
kind: "concept"
title: "MVSplat"
aliases:
  - "concept:470"
  - "mvsplat"
  - "MVSplat"
concept_id: 470
slug: "mvsplat"
node_type: "technique"
concept_origin: "auto"
tags:
  - "Unposed Multi-View Images"
  - "Novel View Synthesis"
  - "Feed-Forward Reconstruction"
  - "VGGT"
  - "Open-Vocabulary Segmentation"
  - "Cross-View Transformer"
  - "3D Reconstruction"
  - "Depth Prediction"
  - "Semantic Field"
  - "Gaussian Splatting"
source_paper_ids:
  - 25
compiled_at: "2026-05-13T11:59:40.503107+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "2d3766dd5e8c65391a6581c740f00bf7be3ce8b8"
---

# MVSplat

## 定义

MVSplat 在当前材料中主要作为 Uni3R 的对比基线出现，而不是被详细展开的方法本身。它属于多视角新视角合成/3D Gaussian Splatting 相关方法，用来衡量 Uni3R 在多视角输入下的重建与渲染质量 [[paper:25]]。

## 在材料中的角色

在 Uni3R 的实验中，MVSplat 被放在 PixelSplat、NoPoSplat、VicaSplat、AnySplat 等方法一起作为 baseline。结果显示，Uni3R 在 RE10k 和 ScanNet 的 4/8 视角设置下整体优于这些基线，包括 MVSplat [[paper:25]]。

具体而言，Uni3R 在 RE10k 4/8 视角下达到 PSNR 26.360/26.629，在 ScanNet 4/8 视角下达到 28.324/26.019；材料将这些结果概括为其多视角合成能力强于 MVSplat 等方法 [[paper:25]]。

## 与 Uni3R 的差异

材料没有提供 MVSplat 的内部架构、训练方式或是否依赖相机位姿等细节，因此不能据此完整比较两者机制。可以确定的是，Uni3R 的核心改进方向是：从无位姿多视角图像一次前馈预测 3D Gaussian primitives，并同时支持新视角渲染、深度估计和开放词汇语义分割 [[paper:25]]。

因此，在这份材料中，MVSplat 更像是一个性能参照点：它代表已有多视角 Gaussian Splatting 路线，而 Uni3R 试图在无位姿输入、前馈速度、几何稳定性和语义理解上进一步扩展这一类方法的能力 [[paper:25]]。
