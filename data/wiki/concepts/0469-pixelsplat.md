---
kind: "concept"
title: "PixelSplat"
aliases:
  - "concept:469"
  - "pixelsplat"
  - "PixelSplat"
concept_id: 469
slug: "pixelsplat"
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
compiled_at: "2026-05-13T11:59:16.517532+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "309a553689c055ae6b30f8436a958263429e9c45"
---

# PixelSplat

## 精简总结

在给定材料中，PixelSplat 只作为 Uni3R 的对比基线出现，并未提供其方法细节、架构设计或训练目标。因此，当前只能确认：PixelSplat 被用于评估无位姿或多视角图像到 3D Gaussian 场景表示任务中的新视角合成能力。

Uni3R 的实验结果显示，在 RE10k 与 ScanNet 的 4/8 视角设置下，Uni3R 的 PSNR 整体优于 PixelSplat，以及 MVSplat、NoPoSplat、VicaSplat、AnySplat 等其他基线 [[paper:25]]。这说明在该论文的评测语境中，PixelSplat 代表了一类已有的 generalizable Gaussian splatting 方法，但在无位姿多视角输入、统一重建与语义理解方面不如 Uni3R 覆盖得完整 [[paper:25]]。

需要注意的是，材料没有说明 PixelSplat 是否支持未标定位姿输入、是否具备语义特征、是否需要逐场景优化，也没有给出其核心机制。因此，关于 PixelSplat 的准确定义和技术定位，需要进一步阅读其原始论文。
