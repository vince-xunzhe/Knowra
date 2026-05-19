---
kind: "concept"
title: "Habitat"
aliases:
  - "concept:38"
  - "habitat"
  - "Habitat"
concept_id: 38
slug: "habitat"
node_type: "dataset"
concept_origin: "auto"
tags:
  - "Permutation Equivariance"
  - "粗到细匹配"
  - "Monocular Depth Estimation"
  - "VSTI-Bench"
  - "Point Map Reconstruction"
  - "Video Depth Estimation"
  - "空间推理"
  - "视觉语言模型"
  - "局部特征"
  - "图像匹配"
  - "稠密对应"
  - "Affine-invariant Camera Pose"
  - "VSI-Bench"
  - "InfoNCE"
  - "相机位姿估计"
  - "视觉定位"
  - "DUSt3R"
  - "单目视频"
  - "Reference-free Reconstruction"
  - "Scale-invariant Point Map"
  - "指令微调"
  - "Feed-forward 3D Reconstruction"
  - "Visual Geometry Reconstruction"
  - "Transformer"
  - "几何编码器"
  - "Camera Pose Estimation"
  - "跨模态融合"
  - "时空推理"
  - "3D重建"
source_paper_ids:
  - 3
  - 9
  - 27
compiled_at: "2026-05-18T14:20:09.592365+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "1b293dcf386b5c606405fdf2cecef4a35004f6fb"
---

# Habitat

## 定义

Habitat 在这里指用于路线规划数据生成的场景/数据来源。根据给定材料，最明确的用法出现在 VLM-3R 的训练数据构建中：作者构建了 207,779 条 3D 重建式训练 QA，其中包含 4,225 条路线规划样本，用来训练模型回答路径、方向、距离和空间关系相关问题 [[paper:3]]。

## 不同视角

在 VLM-3R 中，Habitat 相关数据的价值不只是提供导航路线本身，而是为视觉语言模型补充“空间行动”监督：模型需要从单目视频中推断场景结构、相机运动和物体相对位置，再据此完成路线规划类问答 [[paper:3]]。因此它服务的是从普通视频到三维空间理解的训练目标。

MASt3R 和 π3 的材料没有直接说明使用 Habitat，但它们关注的问题与 Habitat 这类路线规划数据存在方法层面的关联：MASt3R 强调通过三维几何提升图像匹配和定位能力 [[paper:9]]；π3 强调从单图、视频或无序多视角图像中稳定预测相机位姿、深度和局部点云 [[paper:27]]。这些能力都可被视为路线规划数据生成或使用时所依赖的底层几何能力，但输入材料未明确给出它们与 Habitat 的直接数据关系。

## 共识与分歧

共识是，路线规划数据需要的不只是物体识别，而是带有三维结构、相机运动和空间关系的表示。VLM-3R 明确把这类数据用于增强空间与时空推理 [[paper:3]]；MASt3R 和 π3 则分别从图像匹配、位姿/深度/点云预测角度说明三维几何建模对视觉任务的重要性 [[paper:9]][[paper:27]]。

分歧或差异主要在任务定位：VLM-3R 把路线规划样本作为 VLM 指令数据的一部分，目标是让语言模型回答空间问题 [[paper:3]]；MASt3R 更关注像素级匹配和定位 [[paper:9]]；π3 更关注无参考视角、多输入顺序稳定的前馈几何预测 [[paper:27]]。材料中没有显示它们对 Habitat 数据集本身的不同评价。

## 未解问题

给定材料没有说明 Habitat 中路线规划样本的具体生成规则、场景规模、路径标注格式、问题模板或评测指标。因此目前只能确认：它在 VLM-3R 中被用于生成一部分路线规划训练 QA，并支撑模型学习空间行动相关推理 [[paper:3]]。
