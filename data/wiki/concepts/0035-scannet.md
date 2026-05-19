---
kind: "concept"
title: "ScanNet"
aliases:
  - "concept:35"
  - "scannet"
  - "ScanNet"
concept_id: 35
slug: "scannet"
node_type: "dataset"
concept_origin: "auto"
tags:
  - "轨迹规划"
  - "无位姿多视图"
  - "3DGS"
  - "新视角合成"
  - "空间推理"
  - "Pseudo Label"
  - "开放词汇分割"
  - "零样本泛化"
  - "VGGT"
  - "DINOv2 encoder"
  - "相对深度估计"
  - "多任务学习"
  - "车道线检测"
  - "虚拟视角查询"
  - "点图几何监督"
  - "Camera Pose Estimation"
  - "合成数据"
  - "Knowledge Distillation"
  - "持续状态"
  - "点图pointmap"
  - "Teacher-Student 学习"
  - "Permutation Equivariance"
  - "Monocular Depth Estimation"
  - "因果注意力"
  - "Affine-invariant Camera Pose"
  - "Vision Transformer"
  - "VSI-Bench"
  - "多视图几何"
  - "语义3D场"
  - "Depth-Ray 表示"
  - "相机位姿估计"
  - "单目视频"
  - "Dense Prediction Transformer"
  - "Scale-invariant Point Map"
  - "世界坐标系"
  - "Open-Vocabulary Segmentation"
  - "3D目标检测"
  - "Gaussian Splatting"
  - "自监督视觉编码器"
  - "CUT3R"
  - "DPT"
  - "单目深度"
  - "深度解码器"
  - "DINOv2 backbone"
  - "3D重建"
  - "伪标签"
  - "3D Gaussian Splatting"
  - "VSTI-Bench"
  - "在线重建"
  - "Novel View Synthesis"
  - "Depth Prediction"
  - "动态场景"
  - "Transformer Decoder"
  - "Feed-Forward Reconstruction"
  - "教师学生学习"
  - "Semantic 3D Understanding"
  - "Transformer"
  - "几何编码器"
  - "前馈重建"
  - "Cross-View Transformer"
  - "知识蒸馏"
  - "无标注真实图像"
  - "时空推理"
  - "Metric Depth"
  - "端到端自动驾驶"
  - "视觉几何基础模型"
  - "feed-forward 3DGS"
  - "在线3D重建"
  - "Transformer 编码器"
  - "Point Map Reconstruction"
  - "Video Depth Estimation"
  - "单目深度估计"
  - "视觉语言模型"
  - "DINOv2"
  - "Unposed Multi-View Images"
  - "点图 pointmap"
  - "Reference-free Reconstruction"
  - "Semantic Field"
  - "稀疏照片集"
  - "指令微调"
  - "LoRA"
  - "Feed-forward 3D Reconstruction"
  - "视频深度"
  - "Visual Geometry Reconstruction"
  - "Vision-Language-Action"
  - "任意视角重建"
  - "ViT"
  - "DA-2K"
  - "3D Reconstruction"
  - "跨模态融合"
  - "统一解码器"
  - "新视角结构推断"
  - "Vision-Language Model"
  - "Pseudo Depth"
source_paper_ids:
  - 3
  - 5
  - 4
  - 24
  - 6
  - 25
  - 27
compiled_at: "2026-05-18T14:18:48.365472+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "dc230484fddeea90d8903f894b88b7e345c7b54f"
---

# ScanNet

## 定义

ScanNet 是一个面向室内场景的 3D 数据集，在这些材料中主要作为语义 3D 重建、深度估计和新视角合成的训练或评测场景出现。它的价值不只是提供图像序列，而是把“几何是否稳定”“新视角渲染是否准确”“语义是否能投到 3D 中”这些能力放在同一类真实室内场景里检验。

## 不同视角

在前馈式 3D Gaussian 重建中，ScanNet 被用来评估无位姿多视角输入下的新视角合成和语义理解能力。Uni3R 在 ScanNet 的 4/8 视角设置中报告了较强的 PSNR，并在 target view 语义评测中达到 mIoU 0.5584、Acc 0.8268，说明 ScanNet 可同时检验几何重建、渲染质量和开放词汇语义迁移 [[paper:25]]。

从更大的方法背景看，ScanNet 所代表的问题正在从“单一深度或重建任务”转向“统一空间理解”。例如 VLM-3R 关注单目视频中的空间、方位、距离和相机运动推理 [[paper:3]]；Depth Anything V2/3 分别强调单目深度与任意视图几何恢复 [[paper:6]][[paper:5]]；CUT3R 强调连续输入和持久状态下的 3D 感知 [[paper:4]]；π3 则关注无参考视角、置换等变的多图像几何预测 [[paper:27]]。这些方向都与 ScanNet 类室内 RGB-D/多视角数据的评测需求相邻。

## 共识与分歧

共识是：ScanNet 这类真实室内数据集适合检验模型能否把多视角视觉信息组织成一致的 3D 场景，而不仅是逐帧预测。Uni3R 的结果也表明，评测不再局限于深度误差或点云质量，还可以同时看 RGB 新视角合成、深度和语义分割 [[paper:25]]。

分歧主要在方法假设上：有的方法强调从单目或任意视图中直接恢复几何 [[paper:5]][[paper:6]]，有的方法强调随时间维护场景状态 [[paper:4]]，也有方法强调输入顺序不应影响多视角几何输出 [[paper:27]]。这些路线都可能在 ScanNet 类数据上受益，但材料中只有 Uni3R 明确给出了 ScanNet 上的具体结果。

## 未解问题

材料显示，ScanNet 已被用于衡量重建、语义和新视角合成的统一能力，但仍有几个开放点：无位姿输入下几何是否足够稳定、2D 语义蒸馏到 3D 后是否可靠、少视角输入时新视角质量和深度质量能否同时保持，以及这类室内评测能否充分代表更开放的真实世界空间理解。
