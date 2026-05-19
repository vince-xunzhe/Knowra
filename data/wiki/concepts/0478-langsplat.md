---
kind: "concept"
title: "LangSplat"
aliases:
  - "concept:478"
  - "langsplat"
  - "LangSplat"
concept_id: 478
slug: "langsplat"
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
compiled_at: "2026-05-13T12:01:34.732742+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "7a1f692d894f0896bca035b77cb2fa414621d310"
---

# LangSplat

## 定义

LangSplat 在这里作为开放词汇 3D 语义场景表示的 baseline 出现，用于对比 Uni3R 这类同时进行 3D 重建与语义理解的方法 [[paper:25]]。

从给定材料看，LangSplat 代表的是一种把语言/语义特征关联到 3D Gaussian 表示中的思路：场景不仅能被渲染为 RGB 图像，还能通过语义特征与文本特征匹配，实现开放词汇的 3D 分割或查询。Uni3R 延续了这一目标，但重点不在逐场景优化，而是从未标定位姿的多视角图像中一次前馈预测带有几何、外观和语义属性的 3D Gaussian primitives [[paper:25]]。

## 与 Uni3R 的关系

材料中对 LangSplat 的直接描述较少，主要通过 Uni3R 的对比背景体现其定位。Uni3R 将 LangSplat 所代表的“语言语义 + 3D Gaussian”方向推进到一个更统一的框架中：输入多张无位姿图像后，模型直接预测每个 Gaussian 的中心、透明度、颜色、尺度、旋转和语义特征，并通过 Gaussian rasterizer 渲染 RGB、深度和语义特征 [[paper:25]]。

语义侧，Uni3R 使用 LSeg/CLIP 特征蒸馏，把 2D 开放词汇语义迁移到 3D Gaussian 表示中；推理时再用 CLIP 文本特征与像素语义特征做相似度匹配，实现开放词汇分割 [[paper:25]]。这说明 LangSplat 所处的问题脉络是：如何让 3D 场景表示不仅可视化，而且可被自然语言或开放类别语义访问。

## 关键差异

在给定材料中，Uni3R 相比相关 baseline 的主要差异是效率和输入设定。传统逐场景优化方法需要针对每个场景花费较长时间，而 Uni3R 在 8 视角下只需约 0.359 秒；材料中还给出与 Feature-3DGS 的对比，Uni3R 同时提升了 PSNR 和 mIoU [[paper:25]]。

另一个差异是无位姿多视角支持。Uni3R 通过 Cross-View Transformer 融合不同视角，并用 VGGT 生成的点图作为几何引导，使模型在没有相机位姿的情况下仍能稳定预测 3D Gaussian 结构 [[paper:25]]。因此，若把 LangSplat 理解为语义 3DGS baseline，Uni3R 的贡献在于把开放词汇语义、前馈 3DGS 重建、无位姿多视角输入和深度估计合并到同一个系统中。

## 小结

LangSplat 在该材料中不是被详细展开的核心方法，而是作为开放词汇 3D Gaussian 语义表示方向的 baseline。它的核心相关性在于“语言语义特征如何进入 3D Gaussian 场景表示”。Uni3R 则在这个方向上进一步强调统一建模和前馈泛化：不用逐场景优化、不需要 3D 语义标注，并能同时输出新视角渲染、深度和开放词汇语义分割结果 [[paper:25]]。
