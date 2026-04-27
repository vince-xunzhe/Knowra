---
kind: "concept"
title: "Continuous 3D Perceptio…"
concept_id: 17
slug: "continuous-3d-perceptio"
node_type: "paper"
tags:
  - "视觉感知"
  - "表征空间预测"
  - "连续更新"
  - "从视频到3D"
  - "相机参数估计"
  - "静态场景"
  - "相机参数"
  - "多块掩码"
  - "潜在表示"
  - "EMA教师"
  - "相机姿态估计"
  - "Exponential Moving Average Target Encoder"
  - "状态更新"
  - "视觉变换器"
  - "3D重建"
  - "嵌套上下文"
  - "流式重建"
  - "在线学习"
  - "状态更新机制"
  - "视觉Transformers"
  - "ImageNet"
  - "反崩溃"
  - "联合嵌入预测架构"
  - "上下文注意力"
  - "世界模型"
  - "自监督学习"
  - "稀疏照片集合"
  - "Block Masking"
  - "视觉Transformer"
  - "策略优化"
  - "目标编码器"
  - "I-JEPA"
  - "动态场景"
  - "ViT"
  - "3D感知"
  - "Joint-Embedding Predictive Architecture"
  - "动态模拟"
  - "非生成式预训练"
  - "续航性提高"
  - "像素输入"
  - "强化学习"
  - "数据流处理"
  - "Momentum Encoder"
  - "Camera Pose Estimation"
  - "点云图"
  - "基于模型控制"
  - "Vision Transformer"
  - "持久状态"
  - "多视图立体"
  - "几何转换器"
  - "State Update"
  - "深度推理"
  - "三维重建"
  - "SLAM"
  - "Multi-block Masking"
source_paper_ids:
  - 4
  - 8
  - 10
  - 13
compiled_at: "2026-04-26T16:22:55.918973+00:00"
compile_model: "gpt-4o-mini"
---

# Continuous 3D Perceptio…

# Continuous 3D Perception

## 定义
Continuous 3D Perception（连续3D感知）是一种新兴的3D感知框架，旨在通过处理图像序列来持续更新内部状态。这种方法能够将输入图像转换为三维信息，并与已建立的场景信息相结合，从而实现准确的3D重建和对未观察区域结构的预测。该框架适用于多种3D任务，并在多个数据集上展现了优异的性能[[paper:4]]。

## 不同视角
在不同的研究中，Continuous 3D Perception的实现方式和关注点有所不同。例如，[[paper:4]]提出了一种状态更新机制，通过编码输入图像并与历史信息交互，来增强模型的3D重建能力。相较之下，[[paper:8]]引入了几何上下文注意力（GCA），通过维护几何上下文来优化长序列的重建，强调了实时推理的效率。而[[paper:10]]则关注于从像素中学习潜在世界模型，强调通过观察来预测未来状态的能力，展现了模型在规划和稳健性上的优势。

## 共识与分歧
在Continuous 3D Perception的研究中，学者们普遍认可其在动态和静态场景处理中的有效性，以及其在多种图像流输入下的适应能力。然而，关于最佳的状态更新机制和特征提取方法仍存在分歧。例如，[[paper:4]]和[[paper:8]]都强调了状态更新的重要性，但它们在实现细节和关注点上有所不同。此外，尽管[[paper:10]]展示了潜在模型的优势，但其与其他方法在实时性能上的对比仍需进一步探讨。

## 进一步阅读
- [[paper:4]] Continuous 3D Perception Model with Persistent State
- [[paper:8]] Geometric Context Transformer for Streaming 3D Reconstruction
- [[paper:10]] LeWorldModel: End-to-End Learning of Latent World Models from Pixels
- [[paper:13]] Self-Supervised Learning from Images with a Joint-Embedding Predictive Architecture
