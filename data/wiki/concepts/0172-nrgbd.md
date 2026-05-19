---
kind: "concept"
title: "NRGBD"
aliases:
  - "concept:172"
  - "nrgbd"
  - "NRGBD"
concept_id: 172
slug: "nrgbd"
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
compiled_at: "2026-05-18T14:25:12.012436+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "de85e4a0b2035d68719334c773dab6a2788c55f1"
---

# NRGBD

## 定义

NRGBD 在当前材料中被标注为一个用于“点图评测”的数据集。这里的“点图”可理解为视觉几何模型输出的局部或全局 3D point map，用来评估模型从图像、视频或多视角输入中恢复三维几何的能力。

输入材料没有提供 NRGBD 的具体来源、规模、采集方式、场景类型、评价指标或各方法在该数据集上的数值结果，因此不能进一步断定它覆盖室内/室外、RGB-D 真值形式，或是否专门用于深度、相机位姿、点云重建中的某一项任务。

## 不同视角

从涉及论文看，NRGBD 更像是被放在“通用 3D 几何/点图能力评测”语境下使用，而不是某篇论文提出的核心贡献。

CUT3R 关注连续 3D 感知与持久状态，通过状态更新和状态读取，把当前帧信息整合进历史场景状态，并输出点云图和相机到世界的变换参数 [[paper:4]]。在这个视角下，NRGBD 适合作为检验模型是否能稳定产出点图、并利用历史信息补全未观测区域的评测集。

LingBot-Map 关注流式 3D 重建中的实时性、轨迹稳定性和长程记忆，将历史信息拆成 anchor、局部窗口和 trajectory memory，以降低漂移和显存开销 [[paper:8]]。若用于 NRGBD，这类方法更强调视频连续输入下的点图质量是否能随时间保持一致。

π3 则从置换等变的角度处理单图、视频和无序多视角输入，避免依赖固定参考视角，直接预测相机位姿、局部 3D point map 和 confidence map [[paper:27]]。在这一视角下，NRGBD 可用于检验点图预测是否对输入顺序、参考视角选择等因素敏感。

## 共识与分歧

这些论文的共同点是都把点图或 3D 几何预测视为核心输出之一：CUT3R 通过持久状态建模连续场景 [[paper:4]]，LingBot-Map 通过结构化几何上下文记忆支撑流式重建 [[paper:8]]，π3 通过置换等变结构提高多视角几何预测的顺序鲁棒性 [[paper:27]]。因此，NRGBD 在这些工作中的概念位置大概率是一个用于比较点图预测质量的外部基准。

分歧主要在于模型假设不同：CUT3R 强调状态随输入序列持续更新；LingBot-Map 强调长视频流中的有界记忆、实时性和抗漂移；π3 强调无参考视角、输入顺序改变时输出也等变改变。也就是说，同样面对 NRGBD 这类点图评测，不同方法可能分别关注历史状态建模、流式效率与轨迹稳定、以及无序多视角鲁棒性。

## 未解问题

当前材料没有说明 NRGBD 的数据构成和评价协议，因此无法判断它主要考察哪类能力：是单帧 RGB-D 几何恢复、多视角点图一致性，还是视频流式重建稳定性。

也无法从材料中比较三篇论文在 NRGBD 上的实际表现。已有片段提供了 CUT3R、LingBot-Map 和 π3 在其他数据集或任务上的发现，但没有给出 NRGBD 的具体指标，因此不能据此推断哪种方法在 NRGBD 上更优。
