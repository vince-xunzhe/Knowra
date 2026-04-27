---
kind: "paper"
title: "Self-Supervised Learning from Images with a Joint-Embedding Predictive Architecture"
paper_id: 13
slug: "self-supervised-learning-from-images-with-a-joint-embedding-predictive-architecture"
authors:
  - "Mahmoud Assran"
  - "Quentin Duval"
  - "Ishan Misra"
  - "Piotr Bojanowski"
  - "Pascal Vincent"
  - "Michael Rabbat"
  - "Yann LeCun"
  - "Nicolas Ballas"
compiled_at: "2026-04-26T16:18:29.493935+00:00"
compile_model: "gpt-4o-mini"
source_record: "data/paper_records/0013-2301.08243v3.md"
---

# Self-Supervised Learning from Images with a Joint-Embedding Predictive Architecture

## 一句话定位
这篇论文提出了 I-JEPA，一种通过在表征空间预测图像缺失区域来学习更语义化的视觉表示的自监督学习方法。

## 核心贡献
提出 I-JEPA：不重建像素、不靠手工增强，而是在表征空间预测图像缺失区域，学到更语义化的视觉表示。

## 方法
I-JEPA 将自监督任务从像素层面搬到特征层面。它使用一个上下文编码器读取可见图像块，再用一个预测器去预测若干目标图像块的抽象表示。目标表示由一个 EMA 更新的目标编码器产生。关键在于猜出“那里大概是什么语义”，而不是简单地重建缺失区域。

### 架构流程
输入图像被切成不重叠的 [[patch]]。目标分支将整张图送入目标编码器，得到每个 patch 的高层表示，并从中抽取 4 个较大的目标块作为预测答案。上下文分支从同一张图采样一个大的上下文块，去掉与目标块重叠的部分，只把可见 patch 送入上下文编码器。预测器是一个较窄的 [[ViT]]，接收上下文编码器输出，再加上带位置编码的 mask token，分别预测每个目标块位置上的表示。

### 关键公式
- **目标表示生成**：先把整张图切成 N 个 patch，经目标编码器得到每个 patch 的表示；再从这些表示里抽出若干目标块。
- **上下文表示生成**：从图像中采样一个大的上下文区域，删除与目标区域重叠的 patch，再用上下文编码器得到可见部分的表示。
- **预测器映射**：预测器拿到上下文表示和目标位置的 mask token，输出目标位置的预测表示。
- **平均 L2 预测损失**：对每个目标块、每个目标 patch，计算预测表示和目标编码器表示之间的平方距离，再取平均。

## 实验与结论
在 ImageNet 线性评测中，I-JEPA ViT-H/14 训练 300 epoch 得到 79.3% top-1，高于 MAE ViT-H/14 1600 epoch 的 77.2%。在 ImageNet-1% 上，I-JEPA ViT-H/14 为 73.3%，优于 MAE ViT-H/14 的 71.5%。迁移到 CIFAR100/Places205/iNat18 时，I-JEPA 的表现也显著优于 MAE。

## 限制与待解
I-JEPA 依然需要大规模数据和较大 [[ViT]] 才能充分显现优势，训练 ViT-H/14 虽比 MAE 省，但仍需少于 1200 GPU 小时。其性能强依赖掩码策略，目标块太小或上下文太少会导致性能下降。此外，目标编码器 EMA、预测器深度、权重衰减等超参数也比较敏感。

## 涉及概念
- [[自监督学习]] (Self-Supervised Learning, SSL)
- [[ViT]] (Vision Transformer, 视觉Transformer)
- [[JEPA]] (Joint-Embedding Predictive Architecture, 联合嵌入预测架构)
- [[表征预测]] (Representation Prediction, Embedding-space Prediction)
- [[多块掩码]] (Multi-block Masking, Block Masking)
- [[EMA教师]] (Momentum Encoder, Exponential Moving Average Target Encoder)
