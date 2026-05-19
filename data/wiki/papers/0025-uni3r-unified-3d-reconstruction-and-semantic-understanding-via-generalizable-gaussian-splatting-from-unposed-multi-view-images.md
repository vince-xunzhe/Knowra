---
kind: "paper"
title: "Uni3R: Unified 3D Reconstruction and Semantic Understanding via Generalizable Gaussian Splatting from Unposed Multi-View Images"
aliases:
  - "paper:25"
  - "Uni3R: Unified 3D Reconstruction and Semantic Understanding via Generalizable Gaussian Splatting from Unposed Multi-View Images"
  - "uni3r-unified-3d-reconstruction-and-semantic-understanding-via-generalizable-gaussian-splatting-from-unposed-multi-view-images"
paper_id: 25
slug: "uni3r-unified-3d-reconstruction-and-semantic-understanding-via-generalizable-gaussian-splatting-from-unposed-multi-view-images"
authors:
  - "Xiangyu Sun"
  - "Haoyi Jiang"
  - "Liu Liu"
  - "Seungtae Nam"
  - "Gyeongjin Kang"
  - "Xinjie Wang"
  - "Wei Sui"
  - "Zhizhong Su"
  - "Wenyu Liu"
  - "Xinggang Wang"
  - "Eunbyung Park"
paper_category: "三维重建-静态"
compiled_at: "2026-05-13T09:18:07.899978+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "47b6c508713c85ef15c8486f05f32f9664ba04b6"
source_record: "data/paper_records/0025-2508.03643v4.md"
---

# Uni3R: Unified 3D Reconstruction and Semantic Understanding via Generalizable Gaussian Splatting from Unposed Multi-View Images

## 一句话定位

[[Uni3R]] 是一个面向[[无位姿多视角图像]]的前馈式 3D 场景模型：输入若干张未标定位姿的图片和相机内参，一次推理生成同时包含几何、外观和开放词汇语义的[[3D Gaussian Splatting]]场景表示。

## 核心贡献

[[Uni3R]] 的核心贡献是把[[3D Reconstruction]]、[[Novel View Synthesis]]、[[Depth Prediction]] 和 [[Open-Vocabulary Segmentation]] 统一到一个可泛化的 [[Gaussian Splatting]] 表示中。

传统 [[NeRF]]、[[3DGS]] 以及 [[LangSplat]]、[[Feature-3DGS]] 等语义 3D 方法通常依赖逐场景优化，难以快速泛化到新环境。[[Uni3R]] 则直接从任意数量的无位姿多视角图片预测带语义的 3D Gaussian primitives，不需要每个场景单独训练。

它还引入冻结的 [[VGGT]] 点图作为几何监督，通过 [[Point-Map Loss]] 稳定 3D 分布，缓解纯 RGB 监督下 Gaussian 位置漂移或塌陷的问题。

## 方法

[[Uni3R]] 的输入是多张未标定位姿的图像和相机内参。每张图像先加入内参嵌入，再通过 [[DINOv2]] 提取 patch token，并附加 camera token。

模型核心是 [[Cross-View Transformer]]。它交替进行单视角内部注意力和跨视角全局注意力，用来对齐不同照片中的同一物体、边界和空间关系。随后，[[DPT]] 将 token 还原为密集像素特征，多个 MLP head 分别预测每个像素对应的 3D Gaussian 参数。

每个 Gaussian 被表示为：

```text
G_j = {μ_j, α_j, c_j, s_j, r_j, f_sem_j}
```

其中 μ 是 3D 中心，α 是透明度，c 是颜色，s 是尺度，r 是旋转，f_sem 是语义特征。

参数约束包括：

```text
α_j = σ(f^α_j)
s_j = exp(f^s_j) · d_median
r_j = normalize(f^r_j)
```

也就是把透明度限制到 0 到 1，保证尺度为正，并对旋转四元数归一化。

渲染阶段使用 [[Differentiable Rasterization]] / [[Gaussian Rasterizer]] 将 3D Gaussian 投影到目标视角，输出 RGB、深度和语义特征。语义特征沿像素射线按透明度混合：

```text
F_hat = Σ_i f_sem_i α_i Π_{j=1}^{i-1}(1 - α_j)
```

在开放词汇分割中，模型使用 [[CLIP]] 文本特征与渲染得到的像素语义特征做相似度匹配。

训练目标由 RGB 渲染损失、语义损失和几何损失组成：

```text
L_total = L_rgb + λ_sem L_sem + λ_geo L_geo
λ_sem = 0.02, λ_geo = 0.005
```

其中几何监督来自 [[VGGT]] 生成的点图。

## 实验与结论

在 [[ScanNet]] 上，[[Uni3R]] 在 target view 达到 mIoU 0.5584、PSNR 25.53、SSIM 0.8727、LPIPS 0.1380，优于 [[Feature-3DGS]] 的 mIoU 0.4223 和 PSNR 24.49。

在 8 视角重建设置中，与 [[Feature-3DGS]] 相比，[[Uni3R]] 将重建时间从约 40 分钟降到 0.359 秒，PSNR 从 18.17 提升到 24.71，mIoU 从 0.195 提升到 0.554。

在 [[RealEstate10K]] 的 4/8 视角设置中，[[Uni3R]] 达到 PSNR 26.360/26.629，相比强基线 [[VicaSplat]] 平均提升约 2.0 dB。

实验覆盖的数据集包括 [[ScanNet]]、[[ScanNet++]]、[[RealEstate10K]]、[[ACID]]、[[Mip-NeRF360]] 和 [[DTU]]。其中 [[Mip-NeRF360]] 与 [[DTU]] 主要用于跨域泛化评测。

## 历史位置

[[Uni3R]] 继承了 [[3D Gaussian Splatting]] 的显式高效渲染思想，借鉴 [[VGGT]] 的跨帧几何 Transformer，用 [[DINOv2]] 提取视觉 token，并通过 [[LSeg]] / [[CLIP]] 获得开放词汇语义监督。

它也延续了 [[DUSt3R]]、[[NoPoSplat]] 等无位姿重建工作的目标设定，但重点从两视图或成对匹配扩展到多视角全局融合，并把语义理解直接嵌入 3D Gaussian 表示。

在 3D 视觉发展脉络中，[[Uni3R]] 的意义不只是提升新视角合成质量，而是把重建、渲染、深度估计和开放词汇语义理解统一到一个前馈式 3D 表示中，代表了从逐场景优化走向通用 3D 场景理解的一种范式整合。

## 限制与待解

[[Uni3R]] 仍依赖大规模训练数据和多个基础模型，包括 [[VGGT]]、[[DINOv2]]、[[LSeg]] 和 [[CLIP]]，复现成本较高，训练需要 8×A100 或 H100 级别资源。

它的语义能力本质上继承自 2D 视觉语言模型，因此在细粒度类别、遮挡、反光和罕见物体上可能不稳定。

虽然模型支持任意数量视角，但多视角设置下如果缺少几何损失，仍存在模型塌陷风险。在 [[Mip-NeRF360]] 等跨域场景上 PSNR 仍不算很高，说明泛化能力尚未完全解决。

## 涉及概念

[[Uni3R]]、[[3D Reconstruction]]、[[Gaussian Splatting]]、[[3D Gaussian Splatting]]、[[Unposed Multi-View Images]]、[[Feed-Forward Reconstruction]]、[[Open-Vocabulary Segmentation]]、[[Cross-View Transformer]]、[[Cross-Frame Attention]]、[[Novel View Synthesis]]、[[Depth Prediction]]、[[Semantic Field]]、[[VGGT]]、[[DINOv2]]、[[DPT]]、[[CLIP]]、[[LSeg]]、[[Point-Map Loss]]、[[Differentiable Rasterization]]、[[Gaussian Rasterizer]]、[[NeRF]]、[[DUSt3R]]、[[NoPoSplat]]、[[Feature-3DGS]]、[[LangSplat]]、[[VicaSplat]]、[[ScanNet]]、[[ScanNet++]]、[[RealEstate10K]]、[[ACID]]、[[Mip-NeRF360]]、[[DTU]]。
