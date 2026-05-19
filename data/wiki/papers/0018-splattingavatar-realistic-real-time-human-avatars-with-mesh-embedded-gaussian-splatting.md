---
kind: "paper"
title: "SplattingAvatar: Realistic Real-Time Human Avatars with Mesh-Embedded Gaussian Splatting"
aliases:
  - "paper:18"
  - "SplattingAvatar: Realistic Real-Time Human Avatars with Mesh-Embedded Gaussian Splatting"
  - "splattingavatar-realistic-real-time-human-avatars-with-mesh-embedded-gaussian-splatting"
paper_id: 18
slug: "splattingavatar-realistic-real-time-human-avatars-with-mesh-embedded-gaussian-splatting"
authors:
  - "Zhijing Shao"
  - "Zhaolong Wang"
  - "Zhuang Li"
  - "Duotun Wang"
  - "Xiangru Lin"
  - "Yu Zhang"
  - "Mingming Fan"
  - "Zeyu Wang"
paper_category: "三维重建-动态"
compiled_at: "2026-05-07T13:23:40.121483+00:00"
compile_model: "gpt-5.4"
source_signature: "91714e6fe5cce1999d00181827ca23bcdfc973a0"
source_record: "data/paper_records/0018-2403.05087v1.md"
---

# SplattingAvatar: Realistic Real-Time Human Avatars with Mesh-Embedded Gaussian Splatting

## 一句话定位

[[SplattingAvatar]] 是一种面向单目视频的人体数字分身方法，用“[[三角网格]]负责运动控制、[[3D Gaussian Splatting]] 负责高频外观细节”的混合表示，实现了可动画、逼真且实时的人体头像与全身 avatar 渲染。

## 核心贡献

- 提出 [[Mesh-Embedded Gaussian]] 表示：将 [[3D Gaussian]] 直接嵌入到 [[三角网格]] 上，而不是依赖隐式变形场去学习从姿态空间到规范空间的对应。
- 用网格显式驱动高斯的平移、旋转与缩放，使 avatar 继承 [[FLAME]]、[[SMPL]]、[[SMPL-X]] 这类参数化人体模型的可控性与动画兼容性。
- 将 [[Lifted Optimization]] 引入训练过程，允许高斯在网格表面跨三角形移动，即 [[Triangle Walking]]，从而联合优化嵌入位置与外观属性。
- 支持从单目视频训练头部和全身 avatar，并可在 [[Unity]] 中实时运行；文中报告桌面 GPU 超过 300 FPS，[[iPhone 13]] 上约 30 FPS。
- 在多个数据集和设置上取得当时领先的渲染质量，相比 [[PointAvatar]]、[[INSTA]]、[[NHA]]、[[InstantAvatar]]、[[Anim-NeRF]] 等方法更优。

## 方法

### 总体思路

[[SplattingAvatar]] 的核心是“网格管运动，高斯管细节”。

底层使用已配准的人体网格作为运动载体：头部场景使用 [[FLAME]]，全身场景使用 [[SMPL]] / [[SMPL-X]]。在规范空间网格上初始化一批 [[3D Gaussian]]，每个高斯不直接存绝对三维位置，而是存成嵌入参数：
- 三角形编号 \(k\)
- 三角形内重心坐标 \((u,v)\)
- 沿表面法线的位移 \(d\)

这样做的结果是：当网格发生姿态变化时，高斯可以由网格显式带动，无需像一些 [[NeRF]] 类方法那样再去求解模糊的反向变形映射。

### 网格嵌入高斯

论文将高斯中心定义在网格表面附近。

先用重心插值得到三角形内的表面点：

- 式(1)：三角形内点位置  
  \(P = V(k,u,v) = uV_1 + vV_2 + (1-u-v)V_3\)

其含义是，高斯首先附着在某个三角形内部的一个表面点上。

然后再沿插值法线偏移，得到最终高斯中心：

- 式(3)：高斯中心位置  
  \(\mu = P + dn\)

这使得高斯既能贴附在表面，也能表示离表面有一定距离的结构，比如头发、眼镜、衣物边缘等。

这里的方法建立在 [[Phong Surface]] 的表面点与法线计算之上。

### 姿态驱动

高斯不仅位置跟着网格动，旋转和缩放也由网格形变显式驱动。

论文通过对三角形顶点的旋转进行重心插值，得到姿态相关旋转，再与高斯自身在规范空间的旋转组合：

- 式(6)-(7)：姿态相关旋转  
  \(\delta q_{i,t} = uq_1 + vq_2 + (1-u-v)q_3,\quad q_{i,t} = \delta q_{i,t} * q_i\)

直观上，这意味着高斯的朝向会随局部网格形变自然变化。缩放也根据三角形形变进行调整，从而让高斯在不同姿态下维持合理的局部外观。

相比依赖 [[LBS]] 或 [[MLP]] 形变场的方案，这种显式绑定方式更清晰，也更容易兼容传统图形管线中的骨骼动画与 blend shape。

### 渲染

在当前姿态下得到所有高斯后，系统使用 [[Gaussian Splatting]] 的可微光栅化进行渲染，并通过深度排序和 alpha 混合形成最终像素颜色：

- 式(11)：颜色混合  
  \(C = \sum_{i=1}^{N} c_i \alpha_i \prod_{j=1}^{i-1}(1-\alpha_j)\)

即把多个高斯视作沿视线方向叠加的半透明颜色层，前面的高斯会遮挡后面的高斯。

### 训练与优化

训练时，系统同时优化：
- 高斯的颜色、不透明度、旋转、缩放
- 高斯在网格上的嵌入位置

一个关键点是 [[Lifted Optimization]]：如果优化后某个高斯的重心坐标越过当前三角形边界，就通过 [[Triangle Walking]] 将它重新表达为邻接三角形中的点，继续优化。这样高斯既受网格约束，又能在网格表面自由移动到更合适的位置。

此外，论文还结合 densify / clone / split 机制，在细节复杂区域自动分配更多高斯，以提升外观表现。

## 实验与结论

### 任务与设置

论文同时评估了头部 avatar 与全身 avatar 两类场景，训练输入均来自单目视频，并依赖已配准的人体网格。

### 头部头像结果

在头部头像设置中，[[SplattingAvatar]] 相比多种已有方法有明显提升。

文中给出的代表性结果：
- Ours + [[NHA]]：PSNR / SSIM / LPIPS = 28.86 / 0.931 / 0.060
- [[PointAvatar]]：27.84 / 0.913 / 0.067
- [[INSTA]]：26.42 / 0.924 / 0.080
- [[NHA]]：20.29 / 0.883 / 0.145
- Ours + [[FLAME]]：28.19 / 0.931 / 0.063

这些结果说明，该方法在保持可动画性的同时，显著提升了照片级渲染质量。

### 全身结果

在 [[PeopleSnapshot]] 上，论文报告四个角色的 PSNR 均高于 [[InstantAvatar]] 和 [[Anim-NeRF]]。示例包括：

- male-3-casual：从 [[InstantAvatar]] 的 30.91 提升到 33.01
- female-4-casual：从 30.92 提升到 32.57

SSIM 也整体领先。

### 实时性

除了质量提升，[[SplattingAvatar]] 的另一重要结论是其可部署性。

论文报告：
- 在 [[RTX 3090]] 上超过 300 FPS
- 在 [[iPhone 13]] 上约 30 FPS

这表明它不仅优于很多需要体渲染的隐式方法，也真正具备接入 [[Unity]] 等实时系统的潜力。

### 结论

整体上，[[SplattingAvatar]] 证明了一条有效路线：将 [[显式网格]] 的稳定运动控制与 [[Gaussian Splatting]] 的高频细节表达结合起来，可以在单目视频条件下获得兼顾逼真度、可动画性与实时性的数字人表示。

## 限制与待解

- 方法强依赖底层驱动网格的动作表达能力。如果网格无法准确描述衣物、头发或配饰运动，高斯也会随之产生错误运动。
- 训练输入依赖较好的网格配准、分割与相机参数，前处理门槛较高。
- 虽然渲染速度快，但训练过程仍包含 densification、[[Triangle Walking]] 和大量高斯优化，复现复杂度不低。
- 对新姿态的泛化仍受训练姿态覆盖范围限制；论文提到肩部等区域在姿态变化不足时会出现伪影。
- 这种表示更适合补充贴近表面的细节；对于大范围、独立运动的衣物或头发，可能仍需要更解耦的多层网格或单独运动建模。

## 涉及概念

- [[SplattingAvatar]]
- [[3D Gaussian Splatting]]
- [[Gaussian Splatting]]
- [[Mesh-Embedded Gaussian]]
- [[Mesh Embedding]]
- [[Phong Surface]]
- [[Lifted Optimization]]
- [[Triangle Walking]]
- [[Monocular Video]]
- [[Photorealistic Avatar]]
- [[Animatable Avatar]]
- [[FLAME]]
- [[SMPL]]
- [[SMPL-X]]
- [[Unity]]
- [[PeopleSnapshot]]
- [[NHA]]
- [[INSTA]]
- [[PointAvatar]]
- [[InstantAvatar]]
- [[Anim-NeRF]]
- [[NeRF]]
- [[Differentiable Rasterization]]
- [[LBS]]
- [[MLP]]
