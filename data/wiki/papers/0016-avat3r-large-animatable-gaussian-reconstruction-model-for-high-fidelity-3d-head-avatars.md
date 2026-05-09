---
kind: "paper"
title: "Avat3r: Large Animatable Gaussian Reconstruction Model for High-fidelity 3D Head Avatars"
paper_id: 16
slug: "avat3r-large-animatable-gaussian-reconstruction-model-for-high-fidelity-3d-head-avatars"
authors:
  - "Tobias Kirschstein"
  - "Javier Romero"
  - "Artem Sevastopolsky"
  - "Matthias Nießner"
  - "Shunsuke Saito"
paper_category: "三维重建-动态"
compiled_at: "2026-05-07T13:22:35.290550+00:00"
compile_model: "gpt-5.4"
source_signature: "0af6a52a854d0d508128d1231b6b1892af3656ee"
source_record: "data/paper_records/0016-2502.20220v2.md"
---

# Avat3r: Large Animatable Gaussian Reconstruction Model for High-fidelity 3D Head Avatars

## 一句话定位

[[Avat3r]] 是一个把[[大重建模型]]与表情控制结合起来的前馈式头像系统：给定 4 张带相机参数的人头图像和目标表情编码，直接输出高保真、可动画的 [[3D Gaussian Splatting]] 头部表示，用于少图像条件下的[[3D重建]]与头像驱动。

## 核心贡献

- 提出 [[Avat3r]]，可从 4 张输入图像直接回归可渲染、可动画的 3D 头部高斯表示，不再依赖传统高质量头像方法常见的测试时优化。
- 将 [[DUSt3R]] 的几何位置先验与 [[Sapiens]] 的语义特征接入 [[Vision Transformer]] 主干，提升稀疏视图下的重建稳定性与细节质量。
- 通过简单的[[跨注意力]]机制，把表情编码注入中间图像 token，实现对未见身份的表情驱动。
- 采用逐像素预测高斯属性而非固定模板网格绑定，更适合表达头发、发饰等复杂外观。
- 训练时刻意使用不同时间帧作为输入，提高对手机随手拍、轻微动作、单目视频帧这类不完全一致输入的鲁棒性。

## 方法

### 整体思路

[[Avat3r]] 的总体映射可写为：

- 式(1)：`G = AVAT3R(I, π, z_exp)`

其中输入为多张图像、对应相机参数和目标表情编码，输出为一个可渲染、可动画的三维高斯头部表示。

直观上，方法先借助 [[DUSt3R]] 获得每像素的大致三维位置，再借助 [[Sapiens]] 补充语义信息，之后由 [[Vision Transformer]] 在多视角之间聚合结构信息，并通过[[跨注意力]]注入表情控制，最后解码为逐像素的高斯属性并筛选生成最终的 3D Gaussians。

### 输入与先验

输入包含：

- 4 张带相机参数的人头图像
- 一个目标表情编码

几何先验由 [[DUSt3R]] 提供：

- 式(2)：`I_pos, I_conf = DUST3R(I, π)`

即从输入图像和相机参数估计：

- 每像素位置图 `I_pos`
- 每像素置信度图 `I_conf`

语义先验由 [[Sapiens]] 提供，用于补充头发、皮肤、嘴巴、饰品等区域的语义区分能力。

### 主干网络与多视角建模

方法将原图、位置图以及每像素光线相关编码切成 patch，经卷积得到每视角特征；再将 [[Sapiens]] 特征采样到同分辨率后拼接，并通过线性层压回统一维度，形成输入到 [[Vision Transformer]] 的 token。

在主干中，模型通过[[跨视图自注意力]]在同视角与跨视角之间建立匹配并恢复三维结构：

- 式(7)：`h ← SELFATT(h, h)`

这一步的作用是让不同视角的 token 相互交流，对齐同一身份在不同观察角度下的信息。

### 表情注入

[[Avat3r]] 的动画能力来自将表情编码注入中间特征。论文采用简单的[[跨注意力]]机制：

- 式(9)：`h ← CROSSATT(h, f_exp)`

即让图像 token 从表情特征序列中读取信息，从而使几何与外观随目标表情发生变化。论文将这一设计视为把静态少图重建扩展为可动画重建的关键步骤。

### 高斯属性预测与筛选

经过 Transformer 聚合后的低分辨率 token，会被上采样器解码为逐像素高斯属性图，包括：

- 位置
- 尺度
- 旋转
- 颜色
- 不透明度

其中位置和颜色还会加入跳连修正。最终，模型利用 [[DUSt3R]] 的置信度图过滤不可靠像素，仅保留高置信区域对应的高斯：

- 式(13)：`G = { M[x,y] : I_conf[x,y] > τ }`

这样做有两个作用：

- 减少错误位置带来的伪影
- 让不同发型、头发体积的样本自动拥有不同数量的高斯

### 训练目标

训练总损失为：

- 式(18)：`L = λ_l1 L_l1 + λ_ssim L_ssim + λ_lpips L_lpips`

即联合优化：

- 像素误差
- [[SSIM]] 结构相似度
- [[LPIPS]] 感知相似度

目标是在重建对齐与感知细节之间取得平衡。

### 与既有工作的区别

相较于以往方法，[[Avat3r]] 的差异主要在于：

- 从优化式头像重建转向前馈式直接回归
- 从固定模板网格转向逐像素生成 [[3D Gaussian Splatting]] 表示
- 同时引入[[几何先验]]、[[语义先验]]与表情控制
- 用随机时间帧训练提高真实拍摄条件下的鲁棒性

论文将其定位为把[[稀疏视图重建]]与[[可动画头像]]两条路线真正接起来的工作。

## 实验与结论

### 数据集

论文在以下数据集上评测：

- [[Ava256]]
- [[NeRSemble]]

### 主要结果

在 [[Ava256]] 上，[[Avat3r]] 的指标为：

- PSNR 20.7
- SSIM 0.71
- LPIPS 0.33
- AKD 4.8
- CSIM 0.59

对比 [[GPAvatar]]：

- PSNR 19.4
- SSIM 0.69
- LPIPS 0.34
- AKD 5.3
- CSIM 0.31

并且也明显优于 [[HeadNeRF]] 和 [[InvertAvatar]]。

在 [[NeRSemble]] 上，[[Avat3r]] 达到：

- PSNR 20.5
- SSIM 0.75
- LPIPS 0.33
- AKD 3.7
- CSIM 0.50

对比 [[GPAvatar]]：

- PSNR 17.6
- SSIM 0.67
- LPIPS 0.40
- AKD 5.7
- CSIM 0.07

### 结果解读

论文特别强调两类指标上的优势：

- [[CSIM]]：说明生成结果更像输入身份
- [[AKD]]：说明表情驱动后的面部几何更准确

因此，[[Avat3r]] 不只是渲染更清晰，也在身份保持与驱动准确性上更强。

### 消融实验

消融结果显示，完整模型达到：

- PSNR 21.6
- AKD 8.08

并优于去掉以下组件的变体：

- [[DUSt3R]]
- [[Sapiens]]
- 随机时间帧训练

这支持了论文的核心判断：几何先验、语义先验以及时间不一致训练策略都对最终性能有贡献。

### 结论

实验表明，[[Avat3r]] 在少图输入条件下，能够比现有方法更好地兼顾：

- 重建质量
- 身份一致性
- 表情驱动准确性
- 对现实拍摄不一致输入的鲁棒性

## 限制与待解

- 单图输入场景仍需借助外部 [[3D GAN]] 补视角，误差会逐层传递，容易丢失细节并引入视角不一致。
- 推理时需要相机位姿；若头部跟踪估计不准，会直接影响重建质量。
- 当前结果会把输入图中的光照烘焙进头像，在新环境中可能显得不协调，说明材质与光照尚未真正解耦。
- 尽管推理比优化式方法更快，但训练仍依赖大规模高质量多视角数据和较强算力，复现门槛较高。

## 涉及概念

- [[Avat3r]]
- [[3D Gaussian Splatting]]
- [[大重建模型]]
- [[可动画头像]]
- [[稀疏视图重建]]
- [[Vision Transformer]]
- [[跨视图自注意力]]
- [[跨注意力]]
- [[DUSt3R]]
- [[Sapiens]]
- [[Ava256]]
- [[NeRSemble]]
- [[GPAvatar]]
- [[HeadNeRF]]
- [[InvertAvatar]]
- [[LPIPS]]
- [[SSIM]]
- [[CSIM]]
- [[AKD]]
