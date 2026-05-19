---
kind: "concept"
title: "LoFTR"
aliases:
  - "concept:241"
  - "loftr"
  - "LoFTR"
concept_id: 241
slug: "loftr"
node_type: "technique"
concept_origin: "auto"
tags:
  - "局部特征"
  - "图像匹配"
  - "Transformer"
  - "3D重建"
  - "视觉定位"
  - "稠密对应"
  - "InfoNCE"
  - "粗到细匹配"
  - "DUSt3R"
  - "相机位姿估计"
source_paper_ids:
  - 9
compiled_at: "2026-05-13T11:46:33.111962+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "9f7ae207f9491449b5616e43c3d5058dfbe03e92"
---

# LoFTR

## 定义

在该材料中，LoFTR 被作为图像匹配任务中的既有 baseline 出现，尤其是以 LoFTR+KBR 的形式作为 Map-free 定位基准上的已发表最佳结果之一，用来衡量 MASt3R 的改进幅度 [[paper:9]]。

## 在论文中的作用

MASt3R 将图像匹配从传统二维对应问题扩展为结合三维几何的问题：模型同时预测像素级三维点图与局部描述子，并通过三维回归损失和匹配损失联合训练 [[paper:9]]。在这个对比框架下，LoFTR 代表的是已有强图像匹配方法的参照系，而 MASt3R 的核心主张是：仅依赖二维匹配线索不足以应对大视角变化，显式引入三维结构能带来更强鲁棒性和更高精度 [[paper:9]]。

## 关键对比

材料中最明确的对比来自 Map-free 定位测试集：MASt3R 的 VCRE AUC 超过 93%，而已发表最佳 LoFTR+KBR 为 63.4%，绝对提升约 30 个百分点；同时 MASt3R 的中位平移误差降至约 0.36 米量级 [[paper:9]]。这说明在该论文设定下，LoFTR 更像是强二维匹配 baseline，而 MASt3R 通过三维建模、稠密描述子和快速互惠最近邻匹配进一步提升了困难场景下的匹配与定位表现 [[paper:9]]。

## 小结

基于当前唯一材料，LoFTR 的概念定位不是被详细展开的方法本身，而是 MASt3R 用来证明改进幅度的关键 baseline。论文通过与 LoFTR+KBR 的结果对比，强调三维几何约束和专门学习的局部描述子在大视角变化、Map-free 定位等困难任务中的优势 [[paper:9]]。
