---
kind: "paper"
title: "Depth Anything 3: Recovering the Visual Space from Any Views"
aliases:
  - "paper:5"
  - "Depth Anything 3: Recovering the Visual Space from Any Views"
  - "depth-anything-3-recovering-the-visual-space-from-any-views"
paper_id: 5
slug: "depth-anything-3-recovering-the-visual-space-from-any-views"
authors:
  - "Haotong Lin"
  - "Sili Chen"
  - "Jun Hao Liew"
  - "Donny Y. Chen"
  - "Zhenyu Li"
  - "Guang Shi"
  - "Jiashi Feng"
  - "Bingyi Kang"
paper_category: "三维重建-静态"
compiled_at: "2026-05-07T10:02:00.810672+00:00"
compile_model: "gpt-5.4"
source_signature: "165df542e6715656814151471f45c304e11a5811"
source_record: "data/paper_records/0005-2511.10647v1.md"
---

# Depth Anything 3: Recovering the Visual Space from Any Views

## 一句话定位

[[Depth Anything 3]]（DA3）尝试把[[单目深度估计]]与[[多视图几何]]统一到一个极简框架中：基于普通 [[DINOv2]] [[Transformer]] 主干，预测统一的 [[Depth-Ray表示]]，从任意数量、可带或不带相机信息的图像中恢复一致的 3D 空间。

## 核心贡献

- 提出 [[Depth Anything 3]]，面向“任意视图”的几何恢复与位姿估计问题。
- 采用统一的 [[Depth-Ray表示]]，即同时预测 depth 和 ray，而不是拆成多个耦合的几何分支。
- 主干尽量简化：以普通 [[DINOv2]] [[Vision Transformer]] 为核心，通过[[跨视图注意力]]实现多图信息交换。
- 引入 [[Teacher-Student蒸馏]] 方案，用教师模型为真实数据生成高质量伪标签，缓解真实深度标注稀疏和噪声问题。
- 在作者构建的 pose-geometry benchmark 上，相比 [[VGGT]] 与 [[Pi3]] 等方法取得更优结果；同时单目深度超过 [[Depth Anything 2]]，并可作为前端增强前馈式 [[3D Gaussian Splatting]]。

## 方法

### 问题定义与动机

论文关注的问题是：如何统一任意视图下的几何恢复与相机位姿估计。

其动机是，现实场景中输入图像数量和相机信息往往并不固定。如果针对不同输入设定分别设计模型，系统会变得复杂、脆弱，且泛化能力受限。

### 整体思路

DA3 的核心思路是“少做设计、多靠表示”。

- 输入可以是任意数量图像。
- 输入可选带有相机位姿或相机参数，也可以完全不带。
- 模型不堆叠复杂几何模块，而是依赖一个统一主干提取跨视图共享的空间表征。
- 输出层面压缩为统一的 [[Depth-Ray表示]]：每个像素同时预测深度和射线方向，用于恢复一致的 3D 几何。

### 架构流程

根据材料，整体流程如下：

1. 输入图像被切成 patch。
2. patch token 输入普通的 [[DINOv2]] [[Transformer]] 主干。
3. 若存在相机信息，则将其编码为 [[相机条件编码|camera token]]，与视觉 token 一同参与注意力计算。
4. 为了在多视图间交换信息，模型在 [[Transformer]] 中加入输入自适应的[[跨视图注意力]]。
5. 统一主干之后，使用双 [[DPT解码器|DPT]] 头从视觉 token 中解码两类输出：
   - 深度图
   - ray 图
6. 必要时还可配合轻量相机头。
7. depth 与 ray 结合后，可恢复一致的 3D 点云，并进一步用于位姿估计、几何评测和 [[3D Gaussian Splatting]] 渲染。

### 统一几何表示：[[Depth-Ray表示]]

论文把几何恢复压缩到一个更简单的统一表示：[[Depth-Ray表示]]。

- depth 表示每个像素到相机的距离。
- ray 表示像素对应的空间射线方向。

这两个量组合后，可以表达“从相机出发，沿哪个方向走多远”，从而恢复 3D 结构。论文认为这比为不同几何子任务分别设计多个任务头更直接，也更适合统一单目与多视图几何。

### 多视图交互机制

多视图信息交互依赖[[跨视图注意力]]，也即材料中提到的 input-adaptive cross-view attention。其作用是在同一场景的多张图之间建立对应与对照关系，使模型能够利用多视角一致性来改善几何恢复与位姿估计。

### 监督与训练

论文采用 [[Teacher-Student蒸馏]] 训练策略。

- 先用高质量教师模型在真实数据上产生更密、更干净的伪深度标签。
- 学生模型再利用这些伪标签学习跨数据域泛化能力。

材料中还给出了教师相关损失设计，强调除了深度数值本身，还约束局部几何形状。

### 关键公式

#### 式(4) 法向权重

\[
w_i = \sum_{j=0}^{4} \|n_j\| - \|n_i\|
\]

含义：为局部邻域里的各个法向量分配权重，不可靠或尺度偏差更大的点会被降权。

#### 式(5) 加权平均法向

\[
n_m = \sum_{i=0}^{4} w_i \frac{n_i}{\|n_i\|}
\]

含义：对多个邻域法向按权重平均，得到更稳定的局部表面法向。

#### 式(6) 法向损失

\[
L_N = E(\hat{n}_m, n_m) + \sum_{i=0}^{4} E(\hat{n}_i, n_i)
\]

含义：同时约束平均法向和各邻域法向，使预测深度不仅数值正确，也能保持局部几何形状正确。

#### 式(7) 总训练目标

\[
L_T = \alpha L_{grad} + L_{gl} + L_N + L_{sky} + L_{obj}
\]

含义：总损失综合了深度边缘、全局局部几何、表面法向、天空和前景掩码监督，用于训练更稳定的教师模型。

## 实验与结论

### 总体结论

论文报告称，DA3 在作者建立的 pose-geometry benchmark 上全面超过 [[VGGT]] 和 [[Pi3]]，在位姿精度与几何精度上都有明显提升；同时在单目深度上也优于 [[Depth Anything 2]]。

### 主要结果

根据材料，DA3 相比 [[VGGT]] 的平均提升为：

- 相机位姿精度提升 35.7%
- 几何精度提升 23.6%

具体示例包括：

- 在 [[HiRoom]] 上，DA3-Giant 的 F1 从 [[VGGT]] 的 70.2 提升到 95.6
- 在 [[ETH3D]] 上，F1 从 66.7 提升到 87.1
- 在 [[ScanNet++]] 上，F1 从 70.7 提升到 79.3
- 在 [[DTU]] 上，Chamfer Distance 达到更优或接近最优水平

此外，较小规模的 DA3-Large 约 0.36B 参数，仍能在 10 个设置中的 5 个超过 1.19B 参数的 [[VGGT]]。

### 单目深度结果

材料称，其 student 模型在所有评测集上优于 [[Depth Anything 2]]：

- 在 [[ETH3D]] 上提升超过 10%
- 在 [[SINTEL]] 上提升 5.1%

### 下游应用

DA3 还可作为前端增强前馈式 [[3D Gaussian Splatting]]，说明其统一几何表示不仅服务评测任务，也能支持任意视角重建与渲染相关下游。

### 使用的数据集

论文涉及的数据集包括：

- [[HiRoom]]：位姿与几何评测
- [[ETH3D]]：位姿与几何评测
- [[DTU]]：位姿与几何评测
- [[7Scenes]]：位姿与几何评测
- [[ScanNet++]]：训练与评测
- [[AriaDigitalTwin]]：姿态几何训练
- [[SINTEL]]：单目深度评测

## 限制与待解

- 方法成功仍高度依赖强预训练主干、海量公开数据和教师伪标签，复现门槛较高。
- 任意视图统一建模不意味着所有场景都稳定，低纹理、动态物体、反光透明材质、极端遮挡等问题仍可能影响 [[Depth-Ray表示]] 的可靠性。
- 尽管参数效率优于部分大模型，但整体仍是大规模 [[ViT]] 系统，显存与推理成本不低。
- 当前 benchmark 主要集中于静态几何与重建质量，对长期时序一致性、在线 [[SLAM]] 闭环、真实移动机器人噪声环境的检验仍不充分。

## 涉及概念

- [[Depth Anything 3]]
- [[单目深度估计]]
- [[多视图几何]]
- [[视觉几何基础模型]]
- [[Transformer]]
- [[Vision Transformer]]
- [[DINOv2]]
- [[DPT解码器]]
- [[Depth-Ray表示]]
- [[跨视图注意力]]
- [[Teacher-Student蒸馏]]
- [[相机条件编码]]
- [[相机位姿估计]]
- [[3D Gaussian Splatting]]
- [[VGGT]]
- [[Pi3]]
- [[DUSt3R]]
- [[Depth Anything 2]]
- [[SLAM]]
- [[HiRoom]]
- [[ETH3D]]
- [[DTU]]
- [[7Scenes]]
- [[ScanNet++]]
- [[AriaDigitalTwin]]
- [[SINTEL]]
