---
kind: "concept"
title: "Differentiable Rasteriz…"
aliases:
  - "concept:345"
  - "differentiable-rasteriz"
  - "Differentiable Rasteriz…"
concept_id: 345
slug: "differentiable-rasteriz"
node_type: "technique"
concept_origin: "auto"
tags:
  - "Unposed Multi-View Images"
  - "可微光栅化"
  - "Monocular Video"
  - "Human Avatars"
  - "Semantic Field"
  - "Animatable Avatar"
  - "Lifted Optimization"
  - "Unity Rendering"
  - "VGGT"
  - "Gaussian Rasterizer"
  - "Depth Prediction"
  - "Phong Surface"
  - "Real-Time Rendering"
  - "Mesh-Embedded Gaussian"
  - "Photorealistic Avatar"
  - "Gaussian Splatting"
  - "Novel View Synthesis"
  - "Feed-Forward Reconstruction"
  - "Gaussian rendering"
  - "Open-Vocabulary Segmentation"
  - "Cross-View Transformer"
  - "3D Reconstruction"
source_paper_ids:
  - 18
  - 25
compiled_at: "2026-05-13T09:24:11.362664+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "b4c96b8d40f7c63b0ff907bf54a4a788c31fc2d4"
---

# Differentiable Rasteriz…

## 定义

Differentiable Rasterization（可微光栅化）在这些材料中主要指：把 3D 表示投影到 2D 图像平面，并让这个投影与像素混合过程可参与梯度反传，从而用图像、深度或语义监督来训练 3D 表示。

在两篇论文里，它都和 3D Gaussian Splatting 结合：系统将一组 3D Gaussian primitives 投影到目标视角，再通过 rasterizer 输出 RGB、深度或语义特征，并与真值监督计算损失。区别在于，可微光栅化本身不是最终目标，而是连接“3D 表示”和“2D 监督”的训练接口。

## 不同视角

在人像数字分身场景中，可微光栅化承担的是外观监督和可控动画监督的桥梁。SplattingAvatar 将 Gaussian 嵌入到人体网格三角形上，每个 Gaussian 由所在三角形、重心坐标和法线偏移定义位置；网格运动后，高斯随三角形发生平移、旋转和缩放，再通过 Gaussian Splatting 做可微光栅化，与单目视频帧比较损失 [[paper:18]]。这里的重点是：可微渲染让高斯颜色、不透明度、旋转、缩放以及嵌入位置都能从视频监督中被优化。

在通用 3D 重建与语义理解场景中，可微光栅化则更像一个统一输出层。Uni3R 从无位姿多视角图像前馈预测 3D Gaussian 的中心、颜色、透明度、尺度、旋转和语义特征，再由 Gaussian rasterizer 渲染新视角 RGB、深度和语义特征 [[paper:25]]。这里它不只服务于图像重建，还把深度估计和开放词汇语义分割也纳入同一套 3D 表示监督中。

## 共识与分歧

两篇论文的共识是：可微光栅化使 3D Gaussian 成为一种可由 2D 数据直接监督的表示。无论是单目人像视频，还是无位姿多视角图像，模型都可以通过渲染结果和目标图像之间的差异来更新 3D primitives 的几何与外观参数 [[paper:18]] [[paper:25]]。

它们也共同强调 Gaussian Splatting 的实时性和高质量渲染价值。SplattingAvatar 在 Unity 中实现桌面 GPU 超过 300 FPS、手机约 30 FPS [[paper:18]]；Uni3R 相比逐场景优化方法显著提速，在 8 视角下只需 0.359 秒，并提升 PSNR 与语义 mIoU [[paper:25]]。这说明可微光栅化在这些工作中不只是训练技巧，也和可交互、前馈式或实时系统设计相关。

主要分歧在于 3D 表示如何被约束。SplattingAvatar 依赖已配准人体网格，把 Gaussian 绑定在可控 mesh 上，以获得稳定的动作驱动和人体先验 [[paper:18]]。Uni3R 则不依赖每个场景的显式网格或逐场景优化，而是用跨视角 Transformer 直接预测 Gaussian，并用 VGGT 点图作为几何引导 [[paper:25]]。前者强调“可控运动 + 高频细节”，后者强调“泛化重建 + 语义理解”。

## 未解问题

从材料看，可微光栅化监督的效果仍高度依赖 3D 表示的约束方式。SplattingAvatar 的消融显示，如果去掉 trainable embedding，高斯难以紧跟网格运动并产生伪影；如果去掉缩放正则，会出现细长针状伪影 [[paper:18]]。这说明仅靠图像重建损失不足以保证稳定、合理的 3D 结构。

Uni3R 的结果也显示，几何引导仍然关键。去掉 geometry loss 后深度指标明显变差，而完整模型通过 VGGT 点图引导稳定 3D 结构 [[paper:25]]。因此，一个开放问题是：可微光栅化虽然能把 2D 监督传回 3D primitives，但如何避免漂浮、错位、退化形状或几何不稳定，仍需要额外先验、正则或外部几何信号。

## 进一步阅读

如果关注可控人体、人像头像和实时动画，优先读 SplattingAvatar [[paper:18]]。它展示了可微 Gaussian rasterization 如何与 mesh animation 结合。

如果关注无位姿多视角重建、前馈 3DGS 和语义理解，优先读 Uni3R [[paper:25]]。它展示了同一套 Gaussian rasterizer 如何同时服务 RGB、深度和开放词汇语义输出。
