---
kind: "concept"
title: "Waymo"
aliases:
  - "concept:162"
  - "waymo"
  - "Waymo"
concept_id: 162
slug: "waymo"
node_type: "dataset"
concept_origin: "auto"
tags:
  - "点图 pointmap"
  - "虚拟视角查询"
  - "Graph Visual Question Answering"
  - "CUT3R"
  - "世界坐标系"
  - "持续状态"
  - "驾驶行为预测"
  - "InfoNCE"
  - "相机位姿估计"
  - "BLIP-2"
  - "多步推理"
  - "轨迹预测"
  - "LoRA"
  - "Transformer"
  - "视觉定位"
  - "单目深度估计"
  - "Vision Language Model"
  - "DUSt3R"
  - "局部特征"
  - "端到端自动驾驶"
  - "图像匹配"
  - "零样本泛化"
  - "3D重建"
  - "稠密对应"
  - "动态场景"
  - "粗到细匹配"
  - "稀疏照片集"
  - "在线重建"
  - "轨迹离散化"
source_paper_ids:
  - 4
  - 9
  - 14
compiled_at: "2026-05-13T11:41:51.434270+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "606975712523dab78810b8fa5483fa8afc54cad2"
---

# Waymo

## 定义

Waymo 在这些材料中主要作为自动驾驶场景下的零样本泛化评测数据集出现，而不是作为方法本身。它用于检验模型在未见过数据域、传感器配置或驾驶场景上的迁移能力：模型通常在其他数据集上训练，再直接迁移到 Waymo 上测试，以观察其跨域鲁棒性。

## 不同视角

在自动驾驶多模态推理中，Waymo 被用作 DriveLM 的零样本测试场景。[[paper:14]] 中，模型只用 nuScenes 训练，再在 Waymo 上测试，重点考察 Graph VQA 结构是否能帮助视觉语言模型迁移到未见过的传感器配置和数据分布。结果显示，Graph 版本在 Waymo 零样本行为预测上优于 None 和 Chain 结构，说明显式的图结构问答上下文有助于跨数据集泛化。

在三维视觉与几何感知相关工作中，材料并未给出 Waymo 的具体实验细节。[[paper:4]] 讨论的是连续 3D 感知模型 CUT3R，强调模型可处理任意长度图像流、持续更新内部状态，并预测未观测区域；这些能力与自动驾驶长序列场景天然相关，但输入材料没有说明它是否在 Waymo 上做了具体评测。[[paper:9]] 的 MASt3R 关注三维约束下的图像匹配与定位，材料中主要提到 Map-free 等匹配/定位基准，也未提供 Waymo 结果。

## 共识与分歧

这些论文共同指向一个趋势：自动驾驶或大规模视觉场景中的泛化能力，越来越依赖结构化的中间表示，而不只是端到端直接输出。[[paper:14]] 用 Graph VQA 把驾驶拆成感知、预测、规划、行为和运动节点；[[paper:4]] 用持久状态整合历史图像信息；[[paper:9]] 则把二维匹配提升到三维几何空间中处理。

但对于 Waymo 的具体角色，材料覆盖并不均衡。只有 [[paper:14]] 明确把 Waymo 作为零样本泛化测试集，并报告了行为准确率和运动 ADE 等结果。[[paper:4]] 和 [[paper:9]] 提供的是与泛化相关的方法背景，而不是 Waymo 上的直接证据。因此，基于当前材料，Waymo 的核心定位应限定为 DriveLM 中的跨域自动驾驶评测数据集。

## 进一步阅读

若关注 Waymo 作为零样本泛化基准的作用，应优先阅读 [[paper:14]]，尤其是 nuScenes 训练、Waymo 测试的实验部分。若想理解可能支撑这类跨域能力的通用技术背景，可再阅读连续 3D 状态建模的 [[paper:4]] 和三维几何图像匹配的 [[paper:9]]。
