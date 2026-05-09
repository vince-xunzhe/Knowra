---
kind: "paper"
title: "3D Gaussian Blendshapes for Head Avatar Animation"
paper_id: 22
slug: "3d-gaussian-blendshapes-for-head-avatar-animation"
authors:
  - "Shengjie Ma"
  - "Yanlin Weng"
  - "Tianjia Shao"
  - "Kun Zhou"
paper_category: "三维重建-动态"
compiled_at: "2026-05-07T13:25:52.845307+00:00"
compile_model: "gpt-5.4"
source_signature: "a02f1a82dfaa62e10e1cd042df71fe55beebfa0a"
source_record: "data/paper_records/0022-2404.19398v2.md"
---

# 3D Gaussian Blendshapes for Head Avatar Animation

## 一句话定位

将传统 [[Blendshape]] 的可控表情驱动与 [[3D Gaussian Splatting]] 的高质量实时渲染结合，提出 [[Gaussian Blendshapes]] 表示，从单目视频学习可实时驱动的高保真人头头像。

## 核心贡献

- 提出 [[3D Gaussian Blendshapes]]：用一个中性高斯头模和一组表情高斯基形来表示头像，表情时直接按系数线性混合。
- 将 [[FLAME]] 的表情语义、关节结构与 [[LBS|Linear Blend Skinning]] 结合到高斯表示中，使头像具备类似传统参数化人脸模型的可控性。
- 设计一致性引导的高斯差分学习方式：高斯表情基的更新幅度受对应网格位移大小约束，以减少训练时学偏和过拟合。
- 单独建模口腔内部高斯，用于更稳定地表示牙齿和嘴内区域。
- 在效果、速度和控制性之间取得统一：训练约 25 分钟，运行可达 370fps。

## 方法

### 整体思路

方法从一段单目视频出发，先估计每帧的 [[FLAME]] 参数，包括中性网格、50 个表情基、相机、头部与下巴姿态、表情系数以及前景遮罩。

随后：

- 在中性 [[FLAME]] 网格表面采样大量点，初始化为中性高斯模型；
- 根据中性网格到各表情网格的[[形变梯度]]，构造对应的表情高斯基形；
- 额外建立一组口腔内部高斯，用于表示牙齿等结构；
- 对任意帧，先按表情系数做高斯 blendshape 线性混合，再通过 [[LBS]] 施加头部、下巴、眼球、眼睑等姿态变换；
- 最后使用 [[Gaussian Splatting]] 渲染图像。

### 表情线性混合

论文的核心表达与传统 mesh blendshape 一致，只是对象从网格顶点改成了高斯属性：

\[
B_{\psi} = B_0 + \sum_{k=1}^{K} \psi_k \Delta B_k
\]

其中：

- \(B_0\) 是中性高斯模型；
- \(\Delta B_k\) 是第 \(k\) 个表情高斯基形的差分；
- \(\psi_k\) 是表情系数。

这使得表情控制具有明确语义，也避免了很多动态 [[NeRF]] / MLP 解码方法每帧都要进行较重网络前向的开销。

### 姿态驱动

在得到当前表情对应的高斯模型后，再通过 [[LBS]] 施加姿态与关节驱动：

\[
B_{\psi}^{*} = LBS(B_{\psi}, \Theta), \quad B_m^{*} = LBS(B_m, \Theta)
\]

这里口腔内部高斯也会随下巴运动，从而与外部面部动画保持协调。

### 一致性引导的高斯差分

为避免高斯表情基在训练中偏离 [[FLAME]] 提供的表情语义，论文没有直接自由学习每个表情差分，而是在初始化差分基础上引入受网格位移大小控制的可学习增量：

\[
\Delta G_{i,k} = \Delta G^{init}_{i,k} + \max(f(d_{i,k}), 0)\, \Delta \hat{G}_{i,k}
\]

直观上：

- 表情变化大的区域允许更大调整；
- 表情变化小的区域限制更新幅度；
- 从而增强与 [[FLAME]] 表情基的一致性，降低过拟合风险。

### 损失函数

训练目标由三部分组成：

- 颜色重建损失 \(L_{rgb}\)：保证渲染图像贴近输入视频；
- alpha 损失 \(L_{\alpha}\)：约束高斯不要超出头部前景区域；
- 正则项 \(L_{reg}\)：约束口腔内部高斯不要跑出口腔体积。

总损失为：

\[
L = \lambda_1 L_{rgb} + \lambda_2 L_{\alpha} + \lambda_3 L_{reg}
\]

其中 \(L_{rgb}\) 结合了 \(L_1\) 与 [[D-SSIM]]。

## 实验与结论

### 对比对象与数据

论文在 [[INSTA dataset]]、自建数据集和 [[NeRFBlendShape]] 公共数据集上，与 [[INSTA]]、[[PointAvatar]]、[[NeRFBlendShape]] 进行比较。

### 效果提升

以 Table 1 为例：

- 在 `bala` 上，PSNR 从 [[INSTA]] 的 28.66、[[PointAvatar]] 的 29.60 提升到 33.34；
- SSIM 从 0.9130 / 0.9099 提升到 0.9490；
- LPIPS 降到 0.0772。

在自建 `subject4` 上：

- PSNR 达到 34.03；
- 高于 [[INSTA]] 的 30.83 和 [[PointAvatar]] 的 32.57。

与 [[NeRFBlendShape]] 对比时，在 `id3` 上：

- PSNR 从 37.25 提升到 39.83；
- SSIM 从 0.9750 提升到 0.9836；
- 加入 LPIPS loss 后，LPIPS 也优于对方。

### 速度

- 训练约 25 分钟；
- 运行速度约 370fps；
- 超过 [[INSTA]] 的 70fps；
- 约为 [[NeRFBlendShape]] 的 14 倍，也快于 [[PointAvatar]]。

### 结论

这篇工作表明，将传统 [[Blendshape]] 控制范式迁移到 [[3DGS]] 表示中，可以同时获得：

- 清晰的语义控制；
- 更高的渲染质量；
- 更强的细节表现，如皱纹、牙齿、高光；
- 显著优于不少 [[NeRF]] 风格方法的实时性。

它更像是对经典图形学控制方式与现代显式可微渲染表示的一次有效整合。

## 限制与待解

- 依赖 [[FLAME]] 跟踪质量；若表情系数、姿态或网格拟合不准，后续高斯 blendshape 会被带偏。
- 主要针对单目视频中的特定人头重建；对极端姿态、夸张表情、遮挡、大幅头发运动或复杂饰品的泛化可能受限。
- 口腔内部仍需单独设计高斯并加体积约束，说明完整头部拓扑尚未完全统一建模。
- 论文展示了侧视角失败案例，说明当训练视频覆盖不足时，新视角外推仍可能出现瑕疵。
- 虽然比 [[NeRF]] 方法快很多，但训练与实现仍依赖图形学管线、CUDA 和较强显存，复现门槛不低。

## 涉及概念

- [[3D Gaussian Splatting]] / [[Gaussian Splatting]]：实时渲染主表示。
- [[Gaussian Blendshapes]] / [[Blendshape]]：表情线性控制核心。
- [[FLAME]]：提供参数化头模、表情语义与关节结构。
- [[Linear Blend Skinning]] / [[LBS]]：姿态与关节驱动。
- [[形变梯度]]：用于初始化表情高斯。
- [[Photorealistic Avatar]]：目标应用形态。
- [[Facial Animation]]、[[Facial Reenactment]]：主要任务场景。
- [[Monocular Video Reconstruction]]：输入设定。
- [[Real-time Rendering]]：方法的重要工程目标。

## 历史位置

这项工作直接建立在 [[3D Gaussian Splatting]]、传统 [[Blendshape]]、[[FLAME]] 和 [[LBS]] 之上。

其意义不只是把 [[3DGS]] 用到人头重建，而是把传统图形学中“可解释、可驱动”的 rig/表情基思想迁移到了高斯表示里，形成一种兼顾控制性、画质和速度的头像建模范式。它对后续“显式可控的 Gaussian avatar”方向具有明显启发作用，包括：

- 基于高斯的可驱动脸部或全身 avatar；
- 结合语义控制的实时重演；
- 将传统图形学 rig 与现代可微渲染进一步融合的表示学习方法。
