---
kind: "paper"
title: "Image Generators are Generalist Vision Learners"
aliases:
  - "paper:11"
  - "Image Generators are Generalist Vision Learners"
  - "image-generators-are-generalist-vision-learners"
paper_id: 11
slug: "image-generators-are-generalist-vision-learners"
authors:
  - "Valentin Gabeur"
  - "Shangbang Long"
  - "Songyou Peng"
  - "Paul Voigtlaender"
  - "Shuyang Sun"
  - "Yanan Bao"
  - "Karen Truong"
  - "Zhicheng Wang"
  - "Wenlei Zhou"
  - "Jonathan T. Barron"
  - "Kyle Genova"
  - "Nithish Kannen"
  - "Sherry Ben"
  - "Yandong Li"
  - "Mandy Guo"
  - "Suhas Yogin"
  - "Yiming Gu"
  - "Huizhong Chen"
  - "Oliver Wang"
  - "Saining Xie"
  - "Howard Zhou"
  - "Kaiming He"
  - "Thomas Funkhouser"
  - "Jean-Baptiste Alayrac"
  - "Radu Soricut"
paper_category: "三维重建-静态"
compiled_at: "2026-05-07T13:20:09.533886+00:00"
compile_model: "gpt-5.4"
source_signature: "cdfda830219ce38893546c817700c174cd0d7c87"
source_record: "data/paper_records/0011-2604.20329v1.md"
---

# Image Generators are Generalist Vision Learners

## 一句话定位

这篇论文提出 [[Vision Banana]]：在强大的图像生成模型 [[Nano Banana Pro]] 上进行轻量 [[指令微调]]，把多种视觉理解任务统一改写为“生成可解码的 [[RGB可解码输出]] 图像”，从而让图像生成器成为通用视觉学习器。

## 核心贡献

- 论证了[[图像生成预训练]]本身可以提供很强的通用视觉表征，经过少量任务对齐后，能够承担多种视觉理解任务。
- 提出统一接口：不直接输出类别标签、掩码或连续几何量，而是生成一张遵守固定规则、可被反解的 RGB 图像。
- 用同一个模型、通过不同 prompt，在统一框架下完成：
  - [[语义分割]]
  - [[实例分割]]
  - [[指代表达分割]]
  - 单目度量深度估计
  - 表面法线估计
- 在多个 2D 与 3D 基准上达到或逼近专用模型水平，同时基本保留原有文生图和图像编辑能力。

## 方法

### 总体思路

论文的核心不是为每个任务增加专用头或复杂模块，而是把视觉任务输出统一参数化为“可解码图像”。

整体流程可概括为四步：

1. 输入图像，并附加任务指令。
2. 基础模型 [[Nano Banana Pro]] 像执行图像生成一样输出一张 RGB 图。
3. 该 RGB 图遵守任务特定的可解码规则：
   - 分割任务中，不同类别或实例对应不同颜色；
   - 深度任务中，颜色对应连续深度值；
   - 法线任务中，颜色编码三维方向。
4. 将输出 RGB 图按预定义规则反解为标准任务输出，并在标准基准上计算指标。

训练上，作者采用低比例混合数据的轻量 [[指令微调]]，让模型学会“按格式作答”，而不是重新学习视觉能力本身。

### 为什么生成器能做理解

论文的立场是：如果一个图像生成器已经在大规模生成训练中学到了丰富视觉表征，那么视觉理解任务可能只需要一种合适的“输出协议”来激活这些能力。

这里的关键在于把“答案”变成模型最擅长生成的对象——图像本身。这样带来三点收益：

- **统一性**：多个任务共享同一套权重和输出接口；
- **轻量性**：只需少量任务数据进行格式对齐；
- **保真性**：输出仍是 RGB 图像，与原始生成分布更接近，因此更不容易破坏生成能力。

### 可解码 RGB 输出

[[RGB可解码输出]] 是全文最关键的设计。它要求模型生成的可视化结果不仅“看起来像答案”，而且必须能稳定反解为标准评测格式。

对于分割任务，解码规则可写为：

\[
\hat m_k(x)=\mathbb{1}[\|I_{out}(x)-c_k\|<\tau]
\]

含义是：若输出图中某像素颜色足够接近目标颜色 \(c_k\)，就把该像素判给对应类别或实例。

### 深度的可逆颜色编码

论文对连续量的处理尤其关键，重点体现在深度估计上。

先对真实深度做非线性压缩：

\[
f(d,\lambda,c)=1-(1-d/(\lambda c))^{\lambda+1}
\]

其作用是让近处深度分辨率更细、远处更压缩，便于编码到有限的颜色空间中。

然后把压缩后的标量深度映射到 RGB 颜色路径：

\[
\mathbf{y}=g\big(f(d,\lambda,c)\big),\quad \mathbf{y}\in[0,1]^3
\]

推理时再反解回来：

\[
\hat d=f^{-1}\big(g^{-1}(\mathbf{y})\big)
\]

这说明模型对外仍然只生成图像，但系统可以从图像中恢复度量深度值。

## 实验与结论

### 主要实验结果

论文给出的结果表明，一个由图像生成器轻量微调得到的统一模型，在多个视觉理解任务上追平甚至超过专用模型。

2D 任务方面：

- [[Cityscapes]] [[语义分割]] mIoU 为 **0.699**，高于 [[SAM 3]] 的 **0.652**
- [[RefCOCOg]] [[指代表达分割]] cIoU 为 **0.738**，高于 [[SAM 3 Agent]] 的 **0.734**
- [[ReasonSeg]] gIoU 为 **0.793**，高于 [[SAM 3 Agent]] 的 **0.770**
- [[SA-Co/Gold]] [[实例分割]] 为 **0.540**，接近 [[DINO-X]] 的 **0.552**

3D 任务方面：

- 度量深度 4 个数据集平均 δ1 为 **0.929**，超过 [[Depth Anything 3]] 的 **0.918**
- 表面法线平均角误差为 **18.928**，优于 [[Lotus-2]] 的 **19.642**

### 对生成能力的影响

论文还评估了理解增强后对生成能力的影响：

- 文生图 [[GenAI-Bench]] 对基座模型胜率为 **53.5%**
- 图像编辑 [[ImgEdit]] 为 **47.8%**

作者据此认为，该方法在增强理解能力的同时，基本保住了原有生成与编辑能力，没有明显“训坏”基座模型。

### 论文结论

论文的核心结论是：[[图像生成预训练]] 不只是内容合成能力的来源，也可能天然学习到了通用视觉表征。只要采用统一、可逆、可评测的输出协议，再进行轻量 [[指令微调]]，图像生成器就能成为通用视觉基础模型。

## 限制与待解

- 方法依赖“输出可被稳定解码”的设计；若出现颜色漂移、边界混色或格式不稳定，性能会受影响。
- [[实例分割]] 仍略弱于最强专用模型，说明在实例数量未知、目标密集的场景下还有不足。
- 论文建立在私有基座 [[Nano Banana Pro]] 上，外部研究者难以完整复现。
- 轻量主要体现在适配阶段，基座模型本身训练成本仍然极高。
- 将所有任务都转化为图像生成虽然统一，但对某些需要严格离散结构或高精度几何输出的任务，未必总是最直接的接口。

## 涉及概念

- [[Vision Banana]]
- [[Nano Banana Pro]]
- [[图像生成预训练]]
- [[指令微调]]
- [[RGB可解码输出]]
- [[语义分割]]
- [[实例分割]]
- [[指代表达分割]]
- [[Cityscapes]]
- [[RefCOCOg]]
- [[ReasonSeg]]
- [[SA-Co/Gold]]
- [[SAM 3]]
- [[SAM 3 Agent]]
- [[DINO-X]]
- [[Depth Anything 3]]
- [[Lotus-2]]
- [[GenAI-Bench]]
- [[ImgEdit]]
