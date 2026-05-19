---
kind: "paper"
title: "VGGT: Visual Geometry Grounded Transformer"
aliases:
  - "paper:7"
  - "VGGT: Visual Geometry Grounded Transformer"
  - "vggt-visual-geometry-grounded-transformer"
paper_id: 7
slug: "vggt-visual-geometry-grounded-transformer"
authors:
  - "Jianyuan Wang"
  - "Minghao Chen"
  - "Nikita Karaev"
  - "Andrea Vedaldi"
  - "Christian Rupprecht"
  - "David Novotny"
paper_category: "三维重建-静态"
compiled_at: "2026-05-07T10:03:03.707259+00:00"
compile_model: "gpt-5.4"
source_signature: "96900e9b59415d24100f11148170e16f27d18f60"
source_record: "data/paper_records/0007-2503.11651v1.md"
---

# VGGT: Visual Geometry Grounded Transformer

## 一句话定位

[[VGGT]] 是一个统一的前馈式 [[Transformer]] 三维视觉模型：输入同一场景的单张到多张图像，可在一次前向传播中同时预测相机参数、深度图、点图和点跟踪结果，尽量减少传统 [[Structure-from-Motion]] / 多视图几何流程中的匹配、三角化、全局对齐与束调整等慢速优化步骤。

## 核心贡献

- 提出一个统一的大型前馈式三维模型，把多视图三维理解写成联合预测问题：
  \[
  f\big((I_i)_{i=1}^N\big) = (g_i, D_i, P_i, T_i)_{i=1}^N
  \]
  即从多张图像同时输出每张图的相机参数、深度图、点图和跟踪特征。
- 用单一骨干统一覆盖多个传统上彼此分离的任务：[[相机位姿估计]]、[[多视图深度估计]]、点图恢复、点跟踪。
- 采用 [[Alternating-Attention]] 主干，在“单帧内部建模”和“跨帧全局建模”之间交替进行，使模型在网络内部形成几何共识。
- 在推理速度上强调纯前馈优势：通常不到 1 秒，文中给出的代表性数字约为 0.2 秒。
- 实验表明其在相机估计、多视图深度、点云/点图重建、两视图匹配等任务上达到或超过已有方法，并且输出还能作为后续 [[BA]] 的高质量初始化，或作为下游三维骨干用于非刚体跟踪与新视角合成。

## 方法

### 整体思路

[[VGGT]] 的目标是直接从多视图 RGB 图像中统一预测场景三维属性，而不是先做特征匹配、再做几何求解、最后再做优化。其核心是让几何关系在大模型内部被联合建模出来。

用户笔记中的理解与论文摘要一致：模型先将输入图像切成 patch，并借助 [[DINO]] 提取 token 级视觉特征；然后加入专门的 camera token；经过 frame-wise self-attention 与 global self-attention 后，输出相机参数、深度图、点图和点轨迹。

### 架构流程

根据抽取材料，整体流程为：

1. 输入一组同一场景的 RGB 图像。
2. 每张图通过 [[DINO]] 编码为图像 tokens。
3. 为每张图额外加入一个 camera token 和若干 register tokens，用于承载相机状态与全局信息。
4. tokens 进入主干 [[Transformer]]。
5. 主干交替执行两类注意力：
   - **frame-wise self-attention**：只在单张图内部做自注意力，整理局部结构。
   - **global self-attention**：在所有图之间做全局自注意力，汇总跨视角几何关系。
6. 多层交替后：
   - camera token 进入相机预测头，输出内参和外参；
   - 图像 tokens 进入 [[DPT]] 风格稠密头，恢复为高分辨率特征图，并进一步预测深度图、点图、跟踪特征及对应不确定度；
   - 点跟踪部分再接一个 [[CoTracker2]] 风格模块，从特征图采样查询点并与其他图建立相关，输出跨图轨迹。

### 核心机制：[[Alternating-Attention]]

这是论文最关键的结构设计。它不是只做一种统一注意力，而是在两种建模粒度之间交替：

- **单帧内注意力**：先让每张图内部的 token 彼此交互，提炼局部外观和几何线索。
- **跨帧全局注意力**：再让来自不同视角的 token 全局交流，从而建立跨图对应与三维一致性。

抽取材料中给出的 PyTorch 伪代码也对应这一点：先把张量 reshape 成按帧处理的序列做 frame-wise attention，再把所有帧拼成长序列做 global attention。这个设计直接服务于“局部结构整理 + 全局几何整合”的目标。

### 多任务联合训练

训练目标为：
\[
L = L_{\mathrm{camera}} + L_{\mathrm{depth}} + L_{\mathrm{pmap}} + \lambda L_{\mathrm{track}}
\]

即联合优化四类任务：

- 相机预测
- 深度预测
- 点图预测
- 点跟踪

其中相机监督使用 Huber 形式：
\[
L_{\mathrm{camera}} = \sum_{i=1}^{N} \lVert \hat{g}_i - g_i \rVert_{\epsilon}
\]

深度损失同时考虑数值误差、梯度误差与不确定度加权：
\[
L_{\mathrm{depth}} = \sum_{i=1}^{N} \|\Sigma_i^D \odot (\hat{D}_i - D_i)\| + \|\Sigma_i^D \odot (\nabla \hat{D}_i - \nabla D_i)\| - \alpha \log \Sigma_i^D
\]

其含义是：模型不仅预测深度，还预测“自己对该深度有多确定”，从而在困难区域自适应调整监督强度。

### 与既有方法的区别

相较于 [[COLMAP]]、[[VGGSfM]]、[[DUSt3R]]、[[MASt3R]] 等路线，[[VGGT]] 的变化主要在于：

- 不再把三维重建拆成多个独立模块串联执行；
- 不局限于两视图再做后处理拼接；
- 直接在一个大模型中统一建模多图像、多任务、多尺度的几何关系；
- 依赖大规模预训练表征与大规模三维监督数据，而不是大量手工设计的几何归纳偏置。

## 实验与结论

### 总体结论

论文结论是：[[VGGT]] 在多个三维视觉任务上都达到了或超过现有最好方法，并且速度显著更快，说明“统一前馈三维基础模型”是可行的方向。

### 相机位姿估计

在 [[RealEstate10K]] 上：

- 纯前馈 [[VGGT]] 的 AUC@30 为 **85.3**
- [[VGGSfM]] v2 为 **78.9**
- [[MASt3R]] 为 **76.4**
- [[DUSt3R]] 为 **67.7**

在 [[CO3Dv2]] 上：

- 纯前馈 [[VGGT]] 的 AUC@30 为 **88.2**
- [[VGGSfM]] v2 为 **83.4**

若再结合 [[BA]]：

- [[RealEstate10K]] 可提升到 **93.5**
- [[CO3Dv2]] 可提升到 **91.8**

这说明其前馈输出已经很强，同时也能作为后续优化的良好初始化。

### 多视图深度估计

在 [[DTU]] 多视图深度任务上：

- [[DUSt3R]] 的 Overall 为 **1.741**
- [[VGGT]] 降至 **0.382**

这是非常明显的提升，说明该统一模型不仅会“看多张图”，还学到了可用于稠密几何恢复的强表征。

### 点图估计

在 [[ETH3D]] 点图估计上：

- Ours(Depth+Cam) Overall 为 **0.677**
- [[MASt3R]] 为 **0.826**
- [[DUSt3R]] 为 **1.005**

抽取材料还特别指出：由深度和相机反投影得到的点云，反而比模型直接预测的点图头更准确。这也是一个很有意思的实验观察。

### 两视图匹配

在两视图匹配上：

- AUC@20 达到 **73.4**
- 高于 [[Roma]] 的 **70.9**

### 速度与工程意义

材料中强调：

- [[VGGT]] 纯前馈约 **0.2 秒**
- 对比方法通常约 **7–10 秒**

因此它的意义不仅是精度提升，也在于把三维重建从“慢速几何流水线”推进到“快速统一推理”的范式。

## 限制与待解

- **模型规模大**：约 12 亿参数。
- **训练成本高**：需要 64 张 A100 训练 9 天。
- **数据依赖强**：效果依赖大规模、异构且带三维标注的数据。
- **纯前馈尚未完全取代优化**：最优结果仍然能从 [[BA]] 中获益。
- **任务头之间仍有权衡**：点图头未必最优，实验中由深度 + 相机反投影得到的点云更准。
- **可能存在数据饥渴问题**：在强调较少手工几何归纳偏置的前提下，模型对训练分布覆盖范围的依赖可能更强。

## 涉及概念

- [[VGGT]]
- [[Transformer]]
- [[Vision Transformer]]
- [[自注意力]]
- [[Alternating-Attention]]
- [[DINO]]
- [[DPT]]
- [[CoTracker2]]
- [[Structure-from-Motion]]
- [[多视图几何]]
- [[3D重建]]
- [[多视图深度估计]]
- [[相机位姿估计]]
- [[点图]]
- [[点跟踪]]
- [[束调整]]
- [[BA]]
- [[COLMAP]]
- [[VGGSfM]]
- [[DUSt3R]]
- [[MASt3R]]
- [[RealEstate10K]]
- [[CO3Dv2]]
- [[DTU]]
- [[ETH3D]]
- [[Roma]]

## 历史位置

从论文定位看，[[VGGT]] 建立在几条已有路线之上：

- [[Transformer]] 与大规模视觉预训练；
- [[DINO]] 风格 token 表征；
- [[DPT]] 风格稠密预测；
- [[CoTracker2]] 风格跟踪头；
- [[DUSt3R]]、[[MASt3R]]、[[VGGSfM]] 等学习式三维重建方法。

它的重要性不只是“某个指标更高”，而是尝试把原本分散的几何流程收拢进统一大模型中，推动三维视觉向“[[三维基础模型]]”方向发展。
