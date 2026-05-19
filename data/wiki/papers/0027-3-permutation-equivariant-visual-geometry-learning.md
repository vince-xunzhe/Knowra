---
kind: "paper"
title: "π3: Permutation-Equivariant Visual Geometry Learning"
aliases:
  - "paper:27"
  - "π3: Permutation-Equivariant Visual Geometry Learning"
  - "3-permutation-equivariant-visual-geometry-learning"
paper_id: 27
slug: "3-permutation-equivariant-visual-geometry-learning"
authors:
  - "Yifan Wang"
  - "Jianjun Zhou"
  - "Haoyi Zhu"
  - "Wenzheng Chang"
  - "Yang Zhou"
  - "Zizun Li"
  - "Junyi Chen"
  - "Jiangmiao Pang"
  - "Chunhua Shen"
  - "Tong He"
paper_category: "三维重建-静态"
compiled_at: "2026-05-18T14:17:15.098984+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "9edd568d37e3c934255fd7f8500bf95bebc9dd50"
source_record: "data/paper_records/0027-2507.13347v3.md"
---

# π3: Permutation-Equivariant Visual Geometry Learning

## 一句话定位

[[π3]] 是一篇面向[[视觉几何表征学习]]的 [[ICLR 2026]] 论文，核心目标是把多图像 3D 重建从“依赖固定参考视角”的坐标系预测，改成对输入顺序天然稳定的[[置换等变]]集合预测问题。

## 核心贡献

[[π3]] 提出一个不依赖固定参考视角的[[前馈重建]]模型，可从单张图、视频帧或无序多视角图像中直接预测[[相机位姿]]、[[深度]]和[[局部点云]]。

它针对的问题是：传统或现代前馈几何方法常把某一张图像当作参考坐标系，例如 [[DUSt3R]]、[[Fast3R]]、[[FLARE]]、[[VGGT]] 等方法中的参考视角设定会让结果对初始帧、输入顺序和参考选择敏感。[[π3]] 取消这个人为锚点，让输入图片换顺序时，输出也只按相同顺序重排，而不是改变每张图对应的几何解释。

论文的核心改动包括：

- 使用完全[[置换等变]]结构。
- 去掉帧序号位置编码、参考视角 token 和 [[VGGT]] 式 camera token。
- 为每张输入图输出定义在自身相机坐标系中的[[尺度不变点图]]。
- 用[[相对位姿监督]]学习[[仿射不变位姿]]，避免监督绝对全局坐标。
- 保持单次[[前馈重建]]推理，不依赖迭代式优化管线。

## 方法

[[π3]] 的输入是 N 张图像，可以来自视频，也可以是无序多视角图片集合。每张图先通过 [[DINOv2]] 主干网络切分为 patch token，得到单视图视觉特征。

随后模型交替使用两类[[自注意力]]：

- [[View-wise Attention]]：在每张图内部整理局部信息。
- [[Global Attention]]：让不同图像之间交换几何线索。

这种结构始终保留输入图像与输出结果的一一对应关系，不引入“第几帧是参考”的隐式假设。最后 decoder 为每张输入图分别输出[[相机位姿]]、[[局部 3D point map]] 和 [[confidence map]]。

关键约束是[[置换等变]]：

```text
φ(P_π(S)) = P_π(φ(S))
```

含义是：如果把输入图像顺序打乱，模型输出也只按同样顺序打乱；每张图对应的位姿和点图不会被重新解释。

由于单目或多视角重建存在尺度不确定性，训练时使用全序列共享的最优尺度进行对齐：

```text
s* = arg min_s Σ_i Σ_j (1 / z_{i,j}) || s \hat{x}_{i,j} - x_{i,j} ||_1
```

点图重建损失为：

```text
L_points = (1 / 3NHW) Σ_i Σ_j (1 / z_{i,j}) || s* \hat{x}_{i,j} - x_{i,j} ||_1
```

相机监督不使用绝对位姿，而使用任意两张图之间的相对姿态：

```text
\hat{T}_{i←j} = \hat{T}_i^{-1} \hat{T}_j

L_cam = (1 / N(N-1)) Σ_{i≠j} (L_rot(i,j) + λ_trans L_trans(i,j))
```

总训练目标同时约束点云位置、表面法向、置信度和相机相对位姿：

```text
L = L_points + λ_normal L_normal + λ_conf L_conf + λ_cam L_cam
```

直观上，[[π3]] 不选某张图当“世界原点”，而是让每张图先在自身相机坐标系中描述局部形状，再通过跨视角关系把这些局部描述联系起来。因此，图片顺序变化不会改变每张图片应该对应的几何输出。

## 实验与结论

实验最有说服力的部分是跨任务性能和顺序鲁棒性。

在 [[Sintel]] 上，[[π3]] 的相机位姿 ATE 从 [[VGGT]] 的 0.167 降到 0.074；视频深度 Abs Rel 从 0.299 降到 0.233。

在 [[KITTI]] 视频深度任务上，[[π3]] 的 Abs Rel 为 0.038，δ<1.25 为 0.986。

速度方面，[[π3]] 达到 57.4 FPS，快于 [[VGGT]] 的 43.2 FPS，也明显快于 [[DUSt3R]] 的 1.25 FPS。

顺序鲁棒性方面，[[DTU]] 点云 mean accuracy 的标准差为 0.003，而 [[VGGT]] 为 0.033；在 [[ETH3D]] 上几乎为零方差。这说明取消参考视角后，模型对输入顺序和参考帧选择的敏感性显著降低。

论文涉及的训练与评测数据包括 [[GTA-SfM]]、[[CO3D]]、[[WildRGB-D]]、[[Habitat]]、[[ARKitScenes]]、[[TartanAir]]、[[ScanNet]]、[[ScanNet++]] 和 [[BlendedMVG]]。

## 限制与待解

[[π3]] 仍依赖大规模、多源训练数据和接近十亿参数模型，训练成本和复现门槛较高。

它的点云由[[局部 point map]] 和上采样机制产生，在透明物体、复杂光照传输和高不确定区域可能缺少细粒度细节，也可能出现网格状伪影。

虽然[[参考视角]]依赖被弱化，但模型仍需要足够的视角覆盖和合理图像质量。面对极端遮挡、快速非刚体变化或训练分布外场景时，泛化能力仍可能受限。

## 涉及概念

- [[π3]]
- [[置换等变]]
- [[前馈重建]]
- [[视觉几何重建]]
- [[Reference-free Reconstruction]]
- [[相机位姿估计]]
- [[Point Map Reconstruction]]
- [[视频深度估计]]
- [[单目深度估计]]
- [[尺度不变点图]]
- [[仿射不变位姿]]
- [[相对位姿监督]]
- [[自注意力]]
- [[View-wise Attention]]
- [[Global Attention]]
- [[DINOv2]]
- [[DUSt3R]]
- [[Fast3R]]
- [[FLARE]]
- [[VGGT]]
- [[MoGe]]
- [[GTA-SfM]]
- [[CO3D]]
- [[WildRGB-D]]
- [[Habitat]]
- [[ARKitScenes]]
- [[TartanAir]]
- [[ScanNet]]
- [[ScanNet++]]
- [[BlendedMVG]]
- [[Sintel]]
- [[KITTI]]
- [[DTU]]
- [[ETH3D]]
