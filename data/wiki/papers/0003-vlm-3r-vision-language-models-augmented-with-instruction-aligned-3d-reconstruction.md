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
paper_category: "VLM"
compiled_at: "2026-05-07T10:00:55.661112+00:00"
compile_model: "gpt-5.4"
source_signature: "4e6e07c11858d14a5bef27a945663d4e6ee3a5ef"
source_record: "data/paper_records/0003-2505.20279v3.md"
---

# VLM-3R: Vision-Language Models Augmented with Instruction-Aligned 3D Reconstruction

## 一句话定位

[[VLM-3R]] 是一个把[[单目视频]]中的隐式[[3D重建]]信息对齐到[[视觉语言模型]]中的方法，目标是在不依赖深度相机或预建 3D 地图的前提下，增强模型的[[空间推理]]与[[时空推理]]能力。

## 核心贡献

- 提出 [[VLM-3R]] 框架：从普通[[单目视频]]中提取场景结构与相机运动相关的隐式 3D 信息，并将其注入到底座[[视觉语言模型]]中。
- 引入[[几何编码器]]，从多帧视频恢复世界坐标点图与相机位姿估计，再抽取两类[[隐式3D token]]：
  - [[spatial tokens]]：表示场景结构与空间布局
  - [[view tokens]]：表示相机运动与视角变化
- 设计 [[Spatial-Visual-View Fusion]]，通过[[交叉注意力融合]]把几何 token 注入视觉 token，使语义表示带有三维空间与视角变化信息。
- 将“3D 重建式理解”直接纳入[[指令微调]]流程，而不是推理时额外串接显式建图系统。
- 构建 20 万级 3D 重建式指令数据，并提出面向时空推理的新基准 [[VSTI-Bench]]；论文同时讨论了 [[VSI-Bench]]、[[OST-Bench]]、[[OpenEQA]] 上的结果。

## 方法

### 整体思路

[[VLM-3R]] 的输入是一段[[单目视频]]。方法包含两条主分支：

1. [[视觉编码器]]提取每帧的图像语义特征，如物体类别、外观和局部细节。
2. [[几何编码器]]基于多帧对应关系恢复隐式 3D 几何，得到世界坐标点图与相机位姿，并进一步生成[[spatial tokens]]与[[view tokens]]。

随后，模型通过 [[Spatial-Visual-View Fusion]] 将几何信息与视觉语义融合，再投影到语言模型空间，与用户问题一起输入底座[[视觉语言模型]]完成回答生成。

### 架构流程

按论文描述，流程可概括为：

- 输入视频帧 \(\{I_t\}_{t=1}^{T}\)
- 通过[[几何编码器]]估计每帧的世界坐标点图与相机位姿
- 从几何特征中投影出：
  - 场景结构相关的 [[spatial tokens]]
  - 相机运动相关的 [[view tokens]]
- 以视觉特征为 Query、几何 token 为 Key/Value 做[[交叉注意力融合]]
- 将融合后的表示对齐到语言模型输入空间，进行[[指令微调]]与推理

### 关键公式

#### 几何编码器映射

\[
\{\hat{X}^{world}_t, \hat{P}_t\}_{t=1}^{T}=G(\{I_t\}_{t=1}^{T})
\]

含义：把一段视频帧送入[[几何编码器]]，得到每一帧对应的世界坐标点图和相机位姿估计。

#### 空间与视角 token 提取

\[
S_t = \mathrm{Proj}_s(F^{geo}_t),\quad V_t = \mathrm{Proj}_v(F^{geo}_t)
\]

含义：从几何特征中分离出描述场景结构的 [[spatial tokens]] 与描述视角变化的 [[view tokens]]。

#### Spatial-Visual-View Fusion

\[
\tilde{F}^{vis} = \mathrm{CrossAttn}(Q=F^{vis},\ K=[S;V],\ V=[S;V]) + F^{vis}
\]

含义：以视觉特征为查询，通过[[交叉注意力融合]]读取几何 token，并以残差形式写回视觉表示。

#### 指令微调目标

\[
\mathcal{L}_{\mathrm{SFT}} = -\sum_{i=1}^{N}\sum_{l=1}^{L_i}\log p_\theta(y_{i,l}\mid y_{i,<l}, x_i)
\]

含义：训练阶段采用标准自回归[[指令微调]]目标，只是输入中已经包含融合后的几何增强表示。

### 与既有路线的区别

论文将此前方法概括为几类：

- 依赖深度相机或 [[RGB-D]] 输入
- 先运行 [[SLAM]] 或显式[[3D重建]]，再把点云或地图交给语言模型
- 注入几何 token，但常只使用归一化深度，缺少真实尺度信息

[[VLM-3R]] 的区别在于：

- 只依赖[[单目视频]]
- 不要求推理时先显式建图
- 同时建模场景结构与相机运动
- 在[[指令微调]]阶段直接让模型学习“几何增强后的推理”

## 实验与结论

### 主要结果

论文给出的代表性结果包括：

- 在 [[OpenEQA]] 零样本评测上，相比底座 [[LLaVA-NeXT-Video]]：
  - 空间问题准确率从 49.95% 提升到 51.60%
  - 非空间问题从 67.22% 降到 65.54%

这说明 [[VLM-3R]] 主要补强了空间能力，同时总体能力没有明显崩塌。

- 在 [[OpenEQA]] 上，[[VLM-3R]] 取得 61.7% 总体零样本准确率，超过文中列举的 [[GPT-4V]]、[[Gemini-Pro]]、[[Claude 3]]、[[GPT-4]] 和 [[LLaMA2]]。
- 在 [[VSI-Bench]]、[[VSTI-Bench]] 和 [[OST-Bench]] 上达到当前最好或接近使用真值深度信息的方法。

### 结论

论文的核心结论是：仅依赖[[单目视频]]，通过[[几何编码器]]抽取隐式 3D 线索并与视觉语义融合，能够系统性提升[[视觉语言模型]]的静态空间理解与时间变化理解能力，并在一些任务上逼近依赖额外几何真值的方法。

## 限制与待解

- 训练阶段仍然强依赖高质量 3D 元数据、相机位姿和场景标注，数据构建门槛较高。
- 论文中的“动态”更多来自相机移动引起的关系变化，而不是真实复杂动态物体的开放世界建模。
- 几何能力依赖外接[[几何编码器]]；若视频模糊、遮挡严重、纹理弱或视角跨度不足，隐式 3D 表示可能不稳定。
- 空间能力增强对一般视频理解存在轻微副作用，说明不同能力之间的平衡仍需优化。

## 涉及概念

- [[视觉语言模型]]
- [[单目视频]]
- [[3D重建]]
- [[空间推理]]
- [[时空推理]]
- [[指令微调]]
- [[几何编码器]]
- [[视觉编码器]]
- [[隐式3D token]]
- [[spatial tokens]]
- [[view tokens]]
- [[Spatial-Visual-View Fusion]]
- [[交叉注意力融合]]
- [[OpenEQA]]
- [[VSI-Bench]]
- [[VSTI-Bench]]
- [[OST-Bench]]
- [[LLaVA-Video]]
- [[LLaVA-NeXT-Video]]
- [[CUT3R]]
- [[SpatialVLM]]
- [[SpatialRGPT]]
- [[LLaVA-3D]]
- [[ROSS3D]]
- [[VLM4D]]
- [[Video-3D LLM]]
- [[Feature4X]]
- [[SLAM]]
- [[RGB-D]]
