---
kind: "paper"
title: "VGGT: Visual Geometry Grounded Transformer"
paper_id: 7
slug: "vggt-visual-geometry-grounded-transformer"
authors:
  - "Jianyuan Wang"
  - "Minghao Chen"
  - "Nikita Karaev"
  - "Andrea Vedaldi"
  - "Christian Rupprecht"
  - "David Novotny"
compiled_at: "2026-04-26T16:17:08.881359+00:00"
compile_model: "gpt-4o-mini"
source_record: "data/paper_records/0007-2503.11651v1.md"
---

# VGGT: Visual Geometry Grounded Transformer

## 一句话定位
VGGT 是一种前馈神经网络，能够直接从多视图图像中推断场景的全部 3D 属性，简化了传统 3D 重建流程。

## 核心贡献
引入 VGGT，一个能够直接从图像中预估 3D 属性的神经网络，省去了后续几何优化步骤。

## 方法
VGGT 网络的输入是多视图图像，首先将输入图像分割成小块（patch），然后使用 [[DINO]] 模型执行标记化，以提取来自图像的视觉特征。在提取特征后，框架将相机标记添加到模型中，以帮助网络预测相机的参数。接下来，数据通过框架内（frame-wise）自注意层和全局自注意层，这些层的主要作用是帮助模型理解图像内的深度关系和全局信息。最终，网络能够输出相机参数、深度图、点图以及点轨迹的信息。相机参数的预测结果包括相机的内参和外参，内参通常涉及到相机的焦距、主点位置等。整个处理流程在秒级内完成，确保了在实时应用中的效率。

### 关键公式
- **式(1) 转换函数**：每个输入图像映射到其相机参数、深度图、点图及特征网格。
- **式(2) 相机参数**：通过旋转四元数、平移向量和视场参数化。
- **式(3) 特征网格**：用于点追踪的特征网格生成及应用。

## 实验与结论
实验显示，VGGT 在相机参数估计、多视深度估计、点云重构和 3D 点追踪等任务上达到了当前最优性能，显著优于需要后处理的优化方法。

## 限制与待解
模型训练时需要大量含 3D 注释的数据，可能会限制其在某些数据稀缺领域的应用。此外，模型的复杂性可能导致推理时的高计算资源需求。

## 涉及概念
- [[VGGT]]
- [[Transformers]]
- [[自注意机制]]
- [[DINO]]
- [[ScanNet]]
- [[Objaverse]]
