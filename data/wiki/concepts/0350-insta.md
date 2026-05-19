---
kind: "concept"
title: "INSTA"
aliases:
  - "concept:350"
  - "insta"
  - "INSTA"
concept_id: 350
slug: "insta"
node_type: "dataset"
concept_origin: "auto"
tags:
  - "可控动画"
  - "3D Gaussian Splatting"
  - "Real-Time Rendering"
  - "Facial Reenactment"
  - "参数化人脸模型"
  - "Gaussian Blendshapes"
  - "Lifted Optimization"
  - "重演驱动"
  - "新视角合成"
  - "Photorealistic Avatar"
  - "绑定继承"
  - "Phong Surface"
  - "Monocular Video Reconstruction"
  - "Gaussian Splatting"
  - "Head Avatar"
  - "Unity Rendering"
  - "Monocular Video"
  - "Animatable Avatar"
  - "Real-time Rendering"
  - "Mesh-Embedded Gaussian"
  - "多视角视频"
  - "Human Avatars"
  - "FLAME"
  - "Linear Blend Skinning"
  - "Facial Animation"
  - "人脸重建"
source_paper_ids:
  - 18
  - 19
  - 22
compiled_at: "2026-05-13T11:50:54.167846+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "8e427eae9c2aa0b19fc87d9bed5dfc78b91a117a"
---

# INSTA

## 定义

INSTA 在这些材料中主要作为头部头像重建/动画任务的评测数据或评测设置出现，用来比较不同头像表示在渲染质量、感知质量和实时性上的表现。材料没有给出 INSTA 的采集方式、规模、角色数量或标注细节，因此只能确认它被用于头部头像相关实验，而不能进一步描述其数据集构成。

## 不同视角

在高斯头像方法中，INSTA 常被当作衡量新方法相对传统头像方法的参照基准。SplattingAvatar 在头部头像任务中报告其方法优于 INSTA：Ours+NHA 达到 PSNR 28.86、SSIM 0.931、LPIPS 0.060，而 INSTA 为 26.42、0.924、0.080 [[paper:18]]。这说明在该评测设置下，网格嵌入式 Gaussian Splatting 相比 INSTA 表现出更好的重建质量，尤其是 PSNR 和 LPIPS。

GaussianAvatars 也把 INSTA 纳入新视角合成和自重演比较。其 novel-view synthesis 指标达到 PSNR 31.6、SSIM 0.938、LPIPS 0.065，被描述为全面优于 INSTA、PointAvatar 和 AvatarMAV [[paper:19]]；在 self-reenactment 中，GaussianAvatars 的 LPIPS 为 0.076，优于 INSTA 的 0.110 [[paper:19]]。这里 INSTA 更像是评价照片级头部头像质量时的一个重要对照点。

3D Gaussian Blendshapes 则明确提到在 INSTA 与自建数据上进行评测，并在多数场景中优于 INSTA 和 PointAvatar；例如 bala 场景上 PSNR 为 33.34，高于 INSTA 的 28.66 [[paper:22]]。该论文还比较运行速度：其方法达到 370fps，而 INSTA 为 70fps [[paper:22]]。因此，INSTA 在这里不仅提供画质比较，也用于体现实时性能差距。

## 共识与分歧

这些论文的共同点是：INSTA 被用作头部头像领域的既有参照，尤其适合放在新型 3D Gaussian 头像方法的实验表中比较。三篇材料都显示，后续 Gaussian-based 方法在 INSTA 相关评测或与 INSTA 的对比中取得更高画质或更好感知指标 [[paper:18]] [[paper:19]] [[paper:22]]。

分歧主要不在于对 INSTA 数据集本身的解释，而在于各论文使用它的比较角度不同：SplattingAvatar 更强调网格驱动高斯表示在头部头像指标上的优势 [[paper:18]]；GaussianAvatars 更强调多视角照片级头像在 novel-view 和 reenactment 中的感知质量提升 [[paper:19]]；3D Gaussian Blendshapes 则同时强调 INSTA 评测上的画质优势和相对 INSTA 的速度提升 [[paper:22]]。

## 未解问题

输入材料没有说明 INSTA 的数据来源、训练/测试划分、视角设置、角色数量、表情覆盖范围或评价协议。因此，虽然可以确认它是头部头像方法常用的评测参照，但无法仅凭这些片段判断它是否更偏向单目、多视角、新视角合成、自重演，或具体包含哪些场景与人物。
