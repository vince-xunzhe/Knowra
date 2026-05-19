---
kind: "paper"
title: "Self-Supervised Learning from Images with a Joint-Embedding Predictive Architecture"
aliases:
  - "paper:13"
  - "Self-Supervised Learning from Images with a Joint-Embedding Predictive Architecture"
  - "self-supervised-learning-from-images-with-a-joint-embedding-predictive-architecture"
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
paper_category: "世界模型"
compiled_at: "2026-05-07T13:21:08.997576+00:00"
compile_model: "gpt-5.4"
source_signature: "cf0e598e31e8d7f8c7cecad0000a8c34799bef05"
source_record: "data/paper_records/0013-2301.08243v3.md"
---

# Self-Supervised Learning from Images with a Joint-Embedding Predictive Architecture

## 一句话定位

[[I-JEPA]] 是一种面向图像自监督表征学习的 [[JEPA]] 实例：它不重建像素，而是根据可见上下文去预测被遮挡区域的高层表示，从而学习更偏语义、且计算更高效的图像特征。

## 核心贡献

论文的核心贡献是提出 [[I-JEPA]]，把自监督学习中的预测目标从像素空间改到表示空间。具体来说，模型不去补全缺失区域的纹理细节，而是预测这些区域经过 [[目标编码器]] 提取后的表示。

相较于两类已有路线，这个设计处在一个中间位置：

- 相比 [[MAE]]、[[data2vec]]、[[CAE]] 这类重建式方法，[[I-JEPA]] 更少关注低层细节，更强调物体、结构和语义关系。
- 相比 [[BYOL]]、[[DINO]]、[[iBOT]] 等依赖多视图增强的方法，它不依赖强人工数据增强，也不需要同时处理多个增强视图。

论文还强调两个关键设计：

- 使用 [[多块遮挡]] 来构造上下文与目标区域，而不是普通随机遮挡。
- 使用通过 [[指数滑动平均]] 更新的 [[目标编码器]] 生成稳定监督目标，以帮助训练避免塌缩。

## 方法

### 整体思路

[[I-JEPA]] 的训练目标是：给定一张图像的可见上下文区域，让模型预测同一张图像中若干被遮挡目标块的表示。

这可以理解为一种“表示预测”而非“像素重建”：

- 输入图像先被切分为不重叠的 patch。
- 从图中选出若干较大的目标块作为预测对象。
- 剩余可见 patch 组成上下文，送入 [[Vision Transformer]] 形式的 context encoder。
- 目标区域对应的“真实目标表示”由另一个 [[目标编码器]] 提供。
- 一个轻量 predictor 根据上下文表示和目标位置信息，输出每个目标块的预测表示。
- 训练时最小化预测表示与目标表示之间的 L2 距离。

### 架构组成

方法包含三个主要部件：

- **Context encoder**：对可见上下文 patch 编码，主干为 [[Vision Transformer]]。
- **Target encoder**：为目标块产生监督目标；其参数不直接反传，而是通过 [[指数滑动平均]] 从 context encoder 更新。
- **Predictor**：接收上下文编码结果，并结合目标块位置的掩码/位置 token，预测目标块表示。

论文给出的流程是：

1. 输入图像并切分 patch。
2. 采样若干目标块，同时保留其余区域作为上下文。
3. 用 [[目标编码器]] 得到整张图或目标路径上的 patch 表示。
4. 从这些表示中裁出各目标块对应的表示。
5. 用 predictor 基于上下文表示预测各目标块表示。
6. 通过 L2 损失让预测结果对齐目标表示。

### 关键公式

#### 目标表示提取

\[
s_y = f_{\bar{\theta}}(y) = \{s_y^1, \ldots, s_y^N\}
\]

含义：输入图像经过 [[目标编码器]] 后，得到每个 patch 的表示向量。

#### 目标块表示

\[
s_y^{(i)} = \{ s_y^j \}_{j \in B_i}
\]

含义：第 \(i\) 个目标块由若干 patch 组成，其表示就是从整张图的 patch 表示中取出属于该块的那些向量。

#### 目标块表示预测

\[
\hat{s}_y^{(i)} = g_{\phi}(f_{\theta}(x), B_i)
\]

含义：先用上下文编码器处理可见区域 \(x\)，再结合目标块位置 \(B_i\)，由 predictor 输出对目标块表示的预测。

#### 训练损失

\[
\mathcal{L} = \sum_{i=1}^{M} \left\| \hat{s}_y^{(i)} - s_y^{(i)} \right\|_2^2
\]

含义：对每个目标块，最小化预测表示与真实目标表示之间的平方误差。

### 为什么这样设计

论文的动机是：像素重建往往迫使模型学习大量纹理、噪声和精确颜色等低层细节，而视图增强式方法又依赖很强的人为先验。[[I-JEPA]] 通过预测表示而非像素，把学习重点转向更抽象的语义关系。

作者认为，这样的训练方式有几个好处：

- 过滤掉无关纹理和噪声，更容易学习物体部件、空间布局和语义结构。
- 不依赖手工设计的多视图增强，偏置更少。
- 不需要昂贵的像素级解码与重建，训练效率更高。
- 更符合 [[JEPA]] 所强调的“预测抽象表示”而非“复刻输入细节”的思路。

### 掩码策略的重要性

论文特别指出，[[掩码建模]] 的具体策略对效果影响很大。经验上：

- 目标块要足够大；
- 上下文区域要足够分散且包含充分信息；
- 多目标块遮挡优于简单随机遮挡。

这说明 [[I-JEPA]] 的性能不仅来自“预测表示”这一目标，也显著依赖上下文—目标划分方式。

## 实验与结论

### 实验设置与任务

论文在多个数据集上进行预训练和评测，包括：

- [[ImageNet-1K]]：预训练与评测
- [[ImageNet-22K]]：预训练
- [[CIFAR100]]：迁移评测
- [[Place205]]：迁移评测
- [[INat18]]：迁移评测
- [[CLEVR/Count]]：低层视觉评测
- [[CLEVR/Dist]]：低层视觉评测

对比基线包括：

- [[MAE]]
- [[data2vec]]
- [[CAE]]
- [[iBOT]]
- [[DINO]]
- [[MSN]]

### 主要结果

论文中最突出的结果集中在 [[ImageNet-1K]] 线性评估和少标签评估上。

在线性评估中：

- [[I-JEPA]] 的 ViT-H/14 用 300 epoch 达到 79.3。
- 对比之下，[[MAE]] 的 ViT-H/14 训练 1600 epoch 达到 77.2。
- [[I-JEPA]] 的 ViT-H/16@448 达到 81.1，基本追平或略超 [[iBOT]] 的 81.0。

在 1% 标注的 [[ImageNet-1K]] 评估中：

- [[I-JEPA]] 的 ViT-H/14 达到 73.3。
- 这一结果与 [[data2vec]] 的 ViT-L/16 持平，但训练更短。
- 明显高于 [[MAE]] ViT-H/14 的 71.5。
- 更大的 ViT-H/16@448 达到 77.3，高于 [[MSN]] 的 75.7。

### 效率结论

论文强调 [[I-JEPA]] 在训练效率上的优势：

- ViT-H/14 的 [[I-JEPA]] 预训练少于 1200 GPU 小时。
- 相比 [[iBOT]] 的 ViT-S/16，速度快超过 2.5 倍。
- 相比 [[MAE]] 的 ViT-H/14，效率高超过 10 倍。

总体结论是：[[I-JEPA]] 在语义性、效率和可扩展性之间取得了较好的平衡，尤其在线性评估、少标签学习和迁移任务上，优于多种重建式方法，并在计算成本上更有优势。

## 限制与待解

论文材料中提到的局限主要包括：

- 方法对 [[掩码建模]] 策略较为敏感，目标块大小、数量、上下文覆盖方式都会明显影响结果。
- 优势更依赖较大的 [[Vision Transformer]] 和较长预训练，小模型或资源受限场景下未必同样突出。
- 方法更强调语义抽象，对特别依赖精细局部纹理的任务未必总是最佳。
- 复现高性能需要较细致的工程调参，包括 [[目标编码器]] 的 [[指数滑动平均]]、predictor 深度、输出端遮挡等设计。

## 涉及概念

- [[I-JEPA]]
- [[JEPA]]
- [[表示预测]]
- [[联合嵌入预测架构]]
- [[掩码建模]]
- [[多块遮挡]]
- [[目标编码器]]
- [[指数滑动平均]]
- [[Vision Transformer]]
- [[自注意力]]
- [[自监督学习]]
- [[图像表征学习]]

## 历史位置

从研究脉络看，这项工作建立在 [[JEPA]] 思想、能量模型视角和 [[Vision Transformer]] 主干之上，同时吸收了 [[MAE]]、[[data2vec]] 这类掩码预测框架，以及 [[BYOL]]、[[DINO]] 一类教师-学生或目标网络稳定训练思路。

其代表性的意义在于：它推动了自监督视觉从“重建输入”或“对齐视图”进一步转向“预测表示本身”。论文并不把重点放在复原像素，而是主张学习更抽象、更有语义的目标表示，因此可以看作自监督视觉中的一次范式转移尝试。
