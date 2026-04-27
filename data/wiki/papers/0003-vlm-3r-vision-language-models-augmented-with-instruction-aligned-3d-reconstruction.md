---
kind: "paper"
title: "VLM-3R: Vision-Language Models Augmented with Instruction-Aligned 3D Reconstruction"
paper_id: 3
slug: "vlm-3r-vision-language-models-augmented-with-instruction-aligned-3d-reconstruction"
authors:
  - "Zhiwen Fan"
  - "Jian Zhang"
  - "Renjie Li"
  - "Junge Zhang"
  - "Runjin Chen"
  - "Hezhen Hu"
  - "Kevin Wang"
  - "Peihao Wang"
  - "Huaizhi Qu"
  - "Shijie Zhou"
  - "Dilin Wang"
  - "Zhicheng Yan"
  - "Hongyu Xu"
  - "Justin Theiss"
  - "Tianlong Chen"
  - "Jiachen Li"
  - "Zhengzhong Tu"
  - "Zhangyang Wang"
  - "Rakesh Ranjan"
compiled_at: "2026-04-26T16:16:29.494461+00:00"
compile_model: "gpt-4o-mini"
source_record: "data/paper_records/0003-2505.20279v3.md"
---

# VLM-3R: Vision-Language Models Augmented with Instruction-Aligned 3D Reconstruction

## 一句话定位
VLM-3R 是一种增强的视觉语言模型，旨在通过单目视频实现带尺度的3D空间感知和时序空间推理能力。

## 核心贡献
VLM-3R 使得视觉语言模型能够在不依赖深度相机、预建3D地图或点云输入的情况下，理解真实环境中的距离、方向、房间大小、相机运动和物体关系。

## 方法
VLM-3R 的输入为单目 RGB 视频和语言指令。视频帧首先通过普通视觉编码器生成 2D 外观 token，然后通过基于 [[CUT3R]] 的空间编码器提取空间 token 和相机视角 token。接着，这些 token 被拼接成统一的 3D 表示 [[Z3D]]，并通过交叉注意力机制将几何信息注入到视频语言模型中。模型的架构流程如下：

1. 输入视频帧通过图像编码器生成特征。
2. 使用 [[CUT3R]] 提取空间 token 和相机视角 token。
3. 通过 [[Spatial-Visual-View Fusion]] 将空间 token 和视角 token 融合。
4. 通过交叉注意力机制将 2D 视觉 token 与 3D token 结合，得到增强的特征表示。
5. 最后，经过两层投影器对齐到大语言模型的输入维度，并与文本指令 token 拼接，输出答案。

### 关键公式
- **式(1) 3D重建式token化**: 
  \[
  F_t = f_{\text{enc}}(I_t), \quad [z'_t, F'_t], s_t = f_{\text{dec}}([z, F_t], s_{t-1})
  \]
- **3D token拼接**: 
  \[
  Z_{3D} = \text{Concat}(F'_t, z'_t)
  \]
- **式(2) 2D-3D交叉注意力**: 
  \[
  H_{\text{attn}}=\text{softmax}\left(\frac{(H_v W_Q)(Z_{3D} W_K)^T}{\sqrt{d_k}}\right)(Z_{3D} W_V)
  \]
- **式(3) 残差融合**: 
  \[
  H'_v = H_v + H_{\text{attn}}
  \]
- **数值题评测MRA**: 
  \[
  MRA = \frac{1}{10}\sum_{\theta\in\{0.5,0.55,...,0.95\}} \mathbf{1}\left(\frac{|\hat{y}-y|}{y}<1-\theta\right)
  \]

## 实验与结论
在 [[VSI-Bench]] 上，VLM-3R 在空间推理和消融实验中表现出色，相比于 2D-only 基线，Absolute Distance 从 20.2 提升到 49.4，Room Size 从 12.3 提升到 67.1，Relative Direction 从 42.4 提升到 80.5。完整模型在 VSI-Bench 的平均得分为 60.90，高于 [[LLaVA-NeXT-Video ft]] 的 57.74。几何编码器消融实验中，[[CUT3R]] 的得分为 60.9，高于 [[VGGT]] 的 58.1 和 Base 的 57.7。

## 限制与待解
论文指出了一些隐含的限制：
1. 依赖于 [[CUT3R]] 这样的端到端 3D 重建器，若单目视频质量较差，可能导致几何 token 误导语言模型。
2. 当前数据主要集中在静态室内场景，尚不足以处理动态人群、户外交通等复杂场景。
3. 增加的空间编码器和融合模块会增加显存、算力和训练复杂度。
4. 空间训练可能导致轻微的通用能力权衡。

## 涉及概念
- [[VLM]]: 视觉语言模型
- [[视频VLM]]: 处理单目视频输入的视觉语言模型
- [[CUT3R]]: 持续状态 3D 感知模型
- [[3DToken]]: 承载几何与相机信息的空间 token
- [[交叉注意力]]: 融合 2D 与 3D 特征的机制
- [[Spatial-Visual-View Fusion]]: 空间-视觉-视角融合模块
- [[VSTI-Bench]]: 用于评测时序空间理解的基准数据集
