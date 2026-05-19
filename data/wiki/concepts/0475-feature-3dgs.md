---
kind: "concept"
title: "Feature-3DGS"
aliases:
  - "concept:475"
  - "feature-3dgs"
  - "Feature-3DGS"
concept_id: 475
slug: "feature-3dgs"
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
compiled_at: "2026-05-13T12:00:27.733085+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "b2dfdf03a16172628050f2da043522b0a8c46610"
---

# Feature-3DGS

## 定义

Feature-3DGS 在这里主要作为 Uni3R 的对比基线出现，代表一种需要对每个场景进行逐场景优化的特征增强 3D Gaussian Splatting 方法。它同样面向带语义特征的 3DGS 表达，但与 Uni3R 的一次前馈预测不同，Feature-3DGS 需要较长的场景级优化过程，因此在速度和泛化能力上成为 Uni3R 重点对照的对象 [[paper:25]]。

## 与 Uni3R 的差异

从 Uni3R 的实验描述看，Feature-3DGS 的核心限制不在于不能表示语义或渲染场景，而在于其工作方式更接近“逐场景拟合”：每来一个新场景，都需要单独优化，耗时约 40 分钟。相比之下，Uni3R 直接从无位姿多视角图像前馈预测 3D Gaussian primitives，并同时赋予几何、外观和开放词汇语义特征，在 8 视角设置下推理仅需 0.359 秒 [[paper:25]]。

性能上，Uni3R 相比 Feature-3DGS 也显著提升：在给出的对比中，PSNR 从 18.17 提升到 24.71，mIoU 从 0.195 提升到 0.554。这说明在该论文的实验设置下，Feature-3DGS 不仅推理/构建速度慢，而且在新视角合成质量和语义分割效果上也弱于 Uni3R [[paper:25]]。

## 概念定位

因此，在这篇材料中，Feature-3DGS 更适合作为“传统特征 3DGS 基线”的参照点：它体现了把语义特征附着到 3D Gaussian 表达中的方向，但仍依赖逐场景优化；Uni3R 则试图把这一范式推进到可泛化、无位姿、多视角、前馈式的统一 3D 重建与语义理解框架 [[paper:25]]。
