---
kind: "concept"
title: "7-Scenes"
aliases:
  - "concept:171"
  - "7-scenes"
  - "7-Scenes"
concept_id: 171
slug: "7-scenes"
node_type: "dataset"
concept_origin: "auto"
tags:
  - "Permutation Equivariance"
  - "Visual Geometry"
  - "Monocular Depth Estimation"
  - "Point Map Reconstruction"
  - "在线重建"
  - "Video Depth Estimation"
  - "单目深度估计"
  - "Depth Prediction"
  - "动态场景"
  - "Video RoPE"
  - "Trajectory Memory"
  - "Vision Transformer"
  - "Affine-invariant Camera Pose"
  - "点图 pointmap"
  - "相机位姿估计"
  - "Reference-free Reconstruction"
  - "稀疏照片集"
  - "世界坐标系"
  - "Scale-invariant Point Map"
  - "Feed-forward 3D Reconstruction"
  - "Geometric Context Transformer"
  - "Geometric Context Attention"
  - "Visual Geometry Reconstruction"
  - "Transformer"
  - "虚拟视角查询"
  - "SLAM"
  - "Camera Pose Estimation"
  - "CUT3R"
  - "持续状态"
  - "Streaming 3D Reconstruction"
  - "3D重建"
source_paper_ids:
  - 4
  - 8
  - 27
compiled_at: "2026-05-18T14:24:48.435552+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "541475f4029b4e9cbad10a61660b17f6ffb47415"
---

# 7-Scenes

## 定义

7-Scenes 在这些材料中主要作为 3D 视觉任务的评测数据集出现，用于衡量模型在场景几何、相机轨迹或点图相关任务上的表现。给定简介中的“点图评测”定位，它更偏向用于验证模型能否从图像序列或多视角输入中恢复稳定的三维结构，而不只是单帧视觉理解。

在输入材料里，7-Scenes 没有被单独介绍其采集方式、场景类别或标注格式，因此不能展开为数据集说明；它的角色更明确地体现在方法评测中：作为跨场景泛化和室内几何重建能力的一个基准。

## 不同视角

在流式 3D 重建方向，7-Scenes 被用来检验模型在连续视频输入下估计相机轨迹与场景深度的能力。LingBot-Map 在 ETH3D、7-Scenes、Tanks & Temples 上分别取得 0.22、0.08、0.20 的 ATE，作者据此说明其结构化几何记忆不只适用于某一类环境，而是从室内小场景到大尺度户外都有泛化能力 [[paper:8]]。

与此相关，CUT3R 强调持续状态更新：模型在处理图像序列时把新观测整合进内部状态，并通过状态读取预测当前或未观察区域的点云图和相机到世界变换 [[paper:4]]。虽然材料没有给出 CUT3R 在 7-Scenes 上的具体数值，但它与 7-Scenes 这类评测的关系在于：该数据集可用于衡量连续感知模型在逐帧积累场景信息时的几何稳定性。

π3 则从另一个角度切入：它关注无参考视角、置换等变的多图像几何预测，避免输入顺序或参考帧选择影响相机位姿、深度和局部点图结果 [[paper:27]]。材料没有直接说明 π3 在 7-Scenes 上的表现，但其“点图”和多视角几何设定与 7-Scenes 作为点图评测数据集的用途相近。

## 共识与分歧

这些论文共同把 7-Scenes 所代表的评测问题视为“多帧/多视角几何是否稳定”的检验，而不只是图像级识别。核心指标集中在相机轨迹、深度、点云或点图质量上，模型需要在有限观测下维持一致的场景结构。

分歧主要体现在建模假设上。CUT3R 采用持久状态，把历史观测压入可更新的内部表示 [[paper:4]]；LingBot-Map 进一步把历史拆成锚点、局部窗口和轨迹记忆，以控制长序列漂移、速度和显存开销 [[paper:8]]；π3 则削弱序列顺序和参考视角的作用，强调输入置换后输出也应对应置换，从而提升无序多视角输入下的稳定性 [[paper:27]]。

因此，7-Scenes 在这些材料中不是一个被详细分析的数据集对象，而是一个承载方法比较的几何基准：它帮助区分模型是依赖短程局部匹配、长期状态记忆，还是更一般的置换等变几何表示。

## 未解问题

输入材料没有说明 7-Scenes 的具体任务划分、评价协议、点图误差定义或各方法在同一设置下的完整对比。因此，目前只能确认它被用于 3D 重建/轨迹评测，尤其在 LingBot-Map 中报告了 ATE 数值 [[paper:8]]；但无法仅凭这些材料判断不同方法在 7-Scenes 上的全面优劣。
