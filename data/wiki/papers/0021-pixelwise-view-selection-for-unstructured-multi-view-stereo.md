---
kind: "paper"
title: "Pixelwise View Selection for Unstructured Multi-View Stereo"
paper_id: 21
slug: "pixelwise-view-selection-for-unstructured-multi-view-stereo"
authors:
  - "Johannes L. Schönberger"
  - "Enliang Zheng"
  - "Marc Pollefeys"
  - "Jan-Michael Frahm"
paper_category: "三维重建-静态"
compiled_at: "2026-05-07T13:25:21.706823+00:00"
compile_model: "gpt-5.4"
source_signature: "2ad3ac739ca932b3a815112df49e5adbc65b2320"
source_record: "data/paper_records/0021-schoenberger2016mvs.md"
---

# Pixelwise View Selection for Unstructured Multi-View Stereo

## 一句话定位

这篇工作提出了一套面向[[无序图像集]]的高效[[多视图立体]]管线，在[[PatchMatch]]式优化中统一完成[[像素级视图选择]]、深度与[[法线估计]]、[[光度一致性]]与[[几何一致性]]约束，以及后续[[深度图融合]]，用于鲁棒稠密三维重建。

## 核心贡献

- 针对真实场景中视角杂乱、遮挡严重、分辨率不一致的[[无序多视图照片]]，提出了更鲁棒的稠密重建方案。
- 在[[PatchMatch Stereo]]框架里，不再只估计深度，而是联合优化深度与[[表面法线]]，更适合倾斜表面。
- 提出[[像素级视图选择]]：不是为整张参考图固定少量源视图，而是对每个像素动态判断哪些源图像更有帮助。
- 将[[三角化角度]]、[[入射角]]、[[分辨率]]和遮挡信息作为[[几何先验]]写入视图采样分布，引导更有效的 Monte Carlo 采样。
- 将[[多视图几何一致性]]直接纳入优化目标，而不是仅作为后处理检查，减少光度歧义带来的误匹配。
- 配合基于支持度的过滤与[[深度图融合]]，得到跨图像一致的稠密点云和法线。

## 方法

### 整体流程

输入是一组已经通过[[SfM]]恢复相机位姿的无序图像。

对每张参考图，算法为每个像素维护一个深度和法线假设，并通过倾斜平面诱导的单应变换，将参考图局部块投影到源图像中。随后：

1. 用[[双边 NCC]]计算[[光度一致性]]；
2. 基于遮挡概率与多种[[几何先验]]进行[[像素级视图选择]]；
3. 通过类似[[PatchMatch]]的传播与随机采样，不断更新深度和法线；
4. 在更新时同时考虑光度匹配和前后向重投影的[[几何一致性]]；
5. 优化结束后，根据光度支持和几何支持过滤离群点，并进行[[深度图融合]]。

### 深度与法线联合估计

论文的一个关键点是联合估计深度和法线，而不是只估计深度。对应形式为：

\[
(\hat{\theta}_l^{opt}, \hat{n}_l^{opt})=\arg\min_{\theta_l^*, n_l^*}\frac{1}{|S|}\sum_{m\in S}\left(1-\rho_l^m(\theta_l^*, n_l^*)\right)
\]

直观上，这使方法能更自然地建模局部斜面，而不是假设表面总是近似正对相机。

### 可见性与光度建模

论文区分了“可见但匹配差”和“因为遮挡而无法匹配”的情况。其可见/遮挡下的光度似然写为：

\[
P(X_l^m \mid Z_l^m, \theta_l)=
\begin{cases}
\frac{1}{NA}\exp\left(-\frac{(1-\rho_l^m(\theta_l))^2}{2\sigma_\rho^2}\right), & Z_l^m=1 \\
\frac{1}{N}U, & Z_l^m=0
\end{cases}
\]

其中，可见时利用[[光度相似度]] \(\rho\) 打分；若被遮挡，则视为随机无关观测。

### 像素级视图选择与采样分布

论文不是预先为整张图固定源视图，而是对每个像素单独采样源视图。视图采样分布为：

\[
P_l(m)=\frac{q(Z_l^m=1)\,q(\alpha_l^m)\,q(\beta_l^m)\,q(\kappa_l^m)}{\sum_{m=1}^{M} q(Z_l^m=1)\,q(\alpha_l^m)\,q(\beta_l^m)\,q(\kappa_l^m)}
\]

这里综合考虑：

- 可见性概率 \(q(Z_l^m=1)\)
- [[三角化角度]]相关先验
- [[入射角]]相关先验
- [[分辨率]]相关先验

这使采样更集中于真正有信息量的源视图，而不必暴力遍历全部图像。

### 光度与几何一致性的联合代价

论文将[[几何一致性]]直接并入优化目标，定义代价：

\[
\xi_l^m = 1-\rho_l^m + \eta\min(\psi_l^m, \psi_{max})
\]

并进一步优化：

\[
(\hat{\theta}_l^{opt}, \hat{n}_l^{opt})=\arg\min_{\theta_l^*, n_l^*}\frac{1}{|S|}\sum_{m\in S} \xi_l^m(\theta_l^*, n_l^*)
\]

其中：

- \(1-\rho_l^m\) 是[[光度代价]]
- \(\psi_l^m\) 是前后向重投影误差
- \(\eta\) 控制几何项权重
- \(\psi_{max}\) 用于截断过大的几何误差

这一设计的核心作用是：不仅要求颜色对得上，还要求几何回投影前后自洽。

### 融合阶段

优化完成后，方法根据光度支持和几何支持过滤离群点，再将跨图像一致的像素聚成簇并融合，形成带法线的稠密点云。整体上，这是一个[[image-based fusion]]风格的[[深度图融合]]过程。

## 实验与结论

### 评测数据

论文使用了以下数据集或场景：

- [[Middlebury]]
- [[Strecha benchmark]]
- [[100M Internet photos]]
- [[South Building dataset]]

### 结果表现

论文在[[Strecha benchmark]]上取得了当时最强或并列最强的结果。

以文中给出的指标为例：

- 在 Fountain 场景上，误差小于 2cm 时达到 0.827，优于 [[Zheng et al.]] 的 0.769，也略优于 [[Hu and Mordohai]] 的 0.824。
- Fountain 在 10cm 指标上达到 0.975，高于 [[Zheng et al.]] 的 0.929。
- 在 Herzjesu 场景上，2cm 指标为 0.691，高于 [[Zheng et al.]] 的 0.650，也优于多数对比方法。
- Herzjesu 在 10cm 指标上达到 0.931，高于 [[Zheng et al.]] 的 0.844。

对比方法中，材料提到了：

- [[Zheng et al.]]
- [[Hu and Mordohai]]
- [[Furukawa and Pon]]

### 消融结论

论文消融表明，下列设计都会带来提升：

- 联合[[法线估计]]
- [[几何先验]]
- 时间平滑
- [[双边 NCC]]
- [[几何一致性]]

### 大规模可扩展性

在[[100M Internet photos]]的大规模实验中，作者在 4 张 Titan X 上处理 4.1 万张图像，平均每视图约 70 秒，4.2 天完成稠密阶段。这说明该方法不仅在精度和完整率上强，也具备面向大规模互联网照片重建的可扩展性。

### 历史位置

这篇工作直接建立在[[Zheng et al.]]的联合视图选择与深度估计框架、[[PatchMatch stereo]]、基于法线的倾斜平面建模、[[深度图融合]]和[[SfM]]相机求解等思路之上。

其历史意义主要在于：

- 将多项关键设计整合为一套统一、实用的现代[[MVS]]系统；
- 成为[[COLMAP]]稠密重建模块的核心代表方案；
- 推动了无序照片集三维重建在学术界与工业界的默认流程形成。

## 限制与待解

- 依赖较准确的[[SfM]]相机位姿；若前端配准有误，后续深度估计与融合会受到明显影响。
- 本质上仍依赖[[多视图光度一致性]]，因此在大面积无纹理、强反光、透明物体、动态物体和极端光照变化下仍可能失败。
- 虽然通过采样提高了效率，但在大规模场景、高分辨率图像和大量候选视图下，显存与算力开销仍然不低。
- 方法包含较多先验、阈值和工程技巧，复现时需要仔细调参。

## 涉及概念

- [[Multi-View Stereo]]
- [[MVS]]
- [[PatchMatch]]
- [[PatchMatch Stereo]]
- [[pixelwise view selection]]
- [[像素级视图选择]]
- [[depth estimation]]
- [[法线估计]]
- [[geometric consistency]]
- [[photometric consistency]]
- [[depth map fusion]]
- [[unstructured image collections]]
- [[双边 NCC]]
- [[SfM]]
- [[COLMAP]]
- [[Middlebury]]
- [[Strecha benchmark]]
- [[100M Internet photos]]
- [[South Building dataset]]
