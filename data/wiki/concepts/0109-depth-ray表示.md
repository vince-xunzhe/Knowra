---
kind: "concept"
title: "Depth-Ray表示"
aliases:
  - "concept:109"
  - "depth-ray表示"
  - "Depth-Ray表示"
concept_id: 109
slug: "depth-ray表示"
node_type: "technique"
concept_origin: "auto"
tags:
  - "多视图几何"
  - "3D Gaussian Splatting"
  - "任意视角重建"
  - "depth + ray"
  - "Teacher-Student 学习"
  - "相机位姿估计"
  - "视觉几何基础模型"
  - "Depth-Ray 表示"
  - "depth-ray representation"
  - "Transformer"
  - "单目深度估计"
  - "DINOv2"
source_paper_ids:
  - 5
compiled_at: "2026-05-13T10:05:36.237835+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "ce7367f51c03593bce1d757bc93a31316fa852cb"
---

# Depth-Ray表示

## 定义

Depth-Ray 表示是一种用于统一视觉几何恢复的最小几何表示：模型不再分别预测深度、点云、相机等多个复杂目标，而是为每个像素预测两类量：深度和射线方向。深度表示像素对应点到相机的距离，ray 表示该像素在空间中的观察方向；两者结合即可恢复对应的 3D 点，并进一步支持点云构建、位姿估计、几何评测和 3D Gaussian Splatting 等任务 [[paper:5]]。

在 Depth Anything 3 中，Depth-Ray 被用作统一单目深度和多视图几何的核心输出形式。无论输入是一张图还是多张图，也无论是否提供相机位姿，模型都通过同一个普通 DINOv2 Transformer 主干提取视觉 token，再由解码头输出 depth + ray，从而把不同输入条件下的空间恢复问题压缩到同一种几何表述中 [[paper:5]]。

## 方法意义

Depth-Ray 表示的关键价值在于“少做任务拆分，多依赖统一表示”。传统多视图几何系统常把问题拆成深度估计、相机位姿估计、点云预测等多个子任务，而 DA3 的结果显示，直接使用 depth + ray 这种紧凑表示，比堆叠 depth + point cloud + camera 或 depth + camera 等辅助输出更有效 [[paper:5]]。

论文中的消融结果表明，Depth-Ray 在所有数据集和指标上优于其他输出组合；相对于 depth + camera，Auc3 指标接近翻倍。这说明它不是单纯减少输出头，而是在几何结构上提供了更适合统一建模的目标 [[paper:5]]。

## 与架构的关系

Depth-Ray 表示也支撑了 DA3 的极简架构设计。模型主体只是普通 DINOv2 Vision Transformer，并通过跨视图自注意力让多张图像之间交换信息；如果有相机信息，则编码为 camera token 参与注意力计算。统一主干之后，双 DPT 头解码出深度图和 ray 图，必要时再附加轻量相机头 [[paper:5]]。

这意味着 Depth-Ray 并不是孤立的输出格式，而是和“单主干、任意视图输入、统一几何恢复”的设计绑定在一起。论文结果显示，复杂的双 Transformer 风格架构并未带来优势，性能反而低于基线；这强化了作者的观点：表示选择比堆叠复杂模块更关键 [[paper:5]]。

## 已验证效果

在 DA3 中，Depth-Ray 表示带来了多方面收益。几何上，它帮助模型从任意数量视图中恢复一致的 3D 空间；性能上，DA3 在 pose-geometry benchmark 的 20 个设置中取得 18 个最优，相比 VGGT 平均提升 35.7% 的位姿精度和 23.6% 的几何精度 [[paper:5]]。

它也没有牺牲单目能力。配合教师-学生伪标签训练后，单目 student 在所有评测集上超过 Depth Anything 2，其中 ETH3D 提升超过 10%，SINTEL 提升约 5.1% [[paper:5]]。

## 未解问题

当前材料只来自 Depth Anything 3 一篇论文，因此 Depth-Ray 表示的外部验证还有限。已有证据主要说明它在 DA3 框架内优于若干替代表示，并能统一单目与多视图几何；但它是否能迁移到其他主干、其他训练范式，或在更复杂动态场景中保持同样优势，材料中尚未展开 [[paper:5]]。
