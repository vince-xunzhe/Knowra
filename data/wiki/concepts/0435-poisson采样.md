---
kind: "concept"
title: "Poisson采样"
aliases:
  - "concept:435"
  - "poisson采样"
  - "Poisson采样"
concept_id: 435
slug: "poisson采样"
node_type: "technique"
concept_origin: "auto"
tags:
  - "3D Gaussian Splatting"
  - "FLAME"
  - "Facial Reenactment"
  - "Linear Blend Skinning"
  - "Monocular Video Reconstruction"
  - "Gaussian Blendshapes"
  - "Real-time Rendering"
  - "Head Avatar"
  - "Facial Animation"
  - "Poisson disk sampling"
  - "Photorealistic Avatar"
source_paper_ids:
  - 22
compiled_at: "2026-05-13T11:56:09.772349+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "b02899d4b57d8a552fc94f3694e73c38b9bd821c"
---

# Poisson采样

## 定义

Poisson采样在这篇工作中用于初始化 3D Gaussian 分布：方法先在中性 FLAME 网格表面采样大量点，并将这些点初始化为中性高斯模型，作为后续头像重建与表情基学习的空间基础 [[paper:22]]。

## 在方法中的作用

在 3D Gaussian Blendshapes 框架里，初始化采样点不是最终表示，而是可优化的起点。论文先从单目视频估计 FLAME 参数，包括中性网格、表情基、姿态、相机和表情系数；随后在中性网格表面采样点并初始化高斯，再根据中性网格到不同表情网格的形变梯度，把这些高斯变换成对应的表情高斯基形 [[paper:22]]。

因此，Poisson采样可以理解为把连续的人脸表面离散化成一组初始高斯“锚点”。这些锚点之后会参与颜色、透明度、位置、旋转等属性优化，并在动画时通过表情系数线性混合、骨骼蒙皮和 Gaussian splatting 渲染出当前表情 [[paper:22]]。

## 关键意义

该初始化为高斯表情基提供了与 FLAME 网格表面对齐的起始结构，使学习到的 3D Gaussians 更容易保留传统 blendshape 的语义可控性。论文的整体结果表明，这种以网格表面采样初始化、再进行一致性约束和视频重建优化的流程，有助于实现高保真人脸细节和实时渲染速度 [[paper:22]]。

## 局限

输入材料没有展开 Poisson采样本身的数学细节，也没有与其他采样策略做消融比较。因此这里只能确认它在该方法中承担“初始化中性高斯分布”的角色，不能进一步断言它相对随机采样、均匀采样或其他点采样方法的优劣。
