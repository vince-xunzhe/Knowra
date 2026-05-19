---
kind: "paper"
title: "Genie: Generative Interactive Environments"
aliases:
  - "paper:17"
  - "Genie: Generative Interactive Environments"
  - "genie-generative-interactive-environments"
paper_id: 17
slug: "genie-generative-interactive-environments"
authors:
  - "Jake Bruce"
  - "Michael Dennis"
  - "Ashley Edwards"
  - "Jack Parker-Holder"
  - "Yuge (Jimmy) Shi"
  - "Edward Hughes"
  - "Matthew Lai"
  - "Aditi Mavalankar"
  - "Richie Steigerwald"
  - "Chris Apps"
  - "Yusuf Aytar"
  - "Sarah Bechtle"
  - "Feryal Behbahani"
  - "Stephanie Chan"
  - "Nicolas Heess"
  - "Lucy Gonzalez"
  - "Simon Osindero"
  - "Sherjil Ozair"
  - "Scott Reed"
  - "Jingwei Zhang"
  - "Konrad Zolna"
  - "Jeff Clune"
  - "Nando de Freitas"
  - "Satinder Singh"
  - "Tim Rocktäschel"
paper_category: "世界模型"
compiled_at: "2026-05-07T13:23:09.392630+00:00"
compile_model: "gpt-5.4"
source_signature: "16deba193d30051ed270d7108bf80969285028c9"
source_record: "data/paper_records/0017-2402.15391v1.md"
---

# Genie: Generative Interactive Environments

## 一句话定位

[[Genie]] 是一个面向[[世界模型]]的生成式系统：它试图仅从无标注互联网视频中，学出可逐帧控制、可交互的虚拟环境。

## 核心贡献

- 提出 [[Genie]]，把传统[[视频生成]]推进到“可玩环境生成”：模型不只是输出一段视频，而是能在给定起始画面和动作序列后持续生成后续可交互帧。
- 在没有真实动作标签、也没有文本配对的条件下，引入[[潜在动作模型]]（[[LAM]]），从相邻视频帧中无监督发现离散动作。
- 构建了“三段式”框架：[[视频 tokenizer]] 负责离散化视频表示，[[潜在动作模型]]负责抽取潜在动作，[[Dynamics Model]] 负责基于历史画面与动作预测未来。
- 展示了模型的可扩展性：动态模型从 40M 扩到 2.7B 时训练损失持续下降，最终训练出约 10.7B 参数的 [[Genie]]。
- 除 2D 平台游戏外，也在机器人视频上验证了方法的通用性，并显示潜在动作空间还能支持从无动作视频中学习模仿策略。

## 方法

### 整体思路

论文把“从视频学环境”拆成三个步骤：

1. 用 [[VQ-VAE]] + [[时空 Transformer]] 构建[[视频 tokenizer]]，把视频帧压缩为离散 token。
2. 用 [[潜在动作模型]]从历史帧与下一帧之间推断最能解释变化的离散潜在动作。
3. 用基于 [[ST-Transformer]] 的[[MaskGIT]]式动态模型，根据历史视频 token 和动作，逐帧预测下一帧 token，并解码回图像。

推理时，只需要一张起始图像和一串用户选择的潜在动作，模型就能逐步生成一个可交互的视频世界。

### 架构流程

论文中的流程可以概括为：

- 输入视频 \(\mathbf{x}_{1:T}\)，先映射为离散表示 \(\mathbf{z}_{1:T}\)。
- 从视频变化中学习潜在动作序列 \(\tilde{\mathbf{a}}_{1:t}\)。
- 用条件分布
  \[
  p(\hat{z}_t \mid \mathbf{z}_{1:t-1}, \tilde{\mathbf{a}}_{1:t-1})
  \]
  预测下一帧 token。
- 最后通过 tokenizer 解码生成像素帧。

对应文中的几个关键形式化定义：

- 视频离散表示：
  \[
  \mathbf{x}_{1:T} = (x_1, x_2, \ldots, x_T) \in \mathbb{R}^{T \times H \times W \times C},\quad
  \mathbf{z}_{1:T} = (z_1, z_2, \ldots, z_T) \in \mathbb{I}^{T \times D}
  \]
- 潜在动作序列：
  \[
  \tilde{\mathbf{a}}_{1:t} = (\tilde{a}_1, \ldots, \tilde{a}_t)
  \]
- 下一帧预测目标：
  \[
  p(\hat{z}_t \mid \mathbf{z}_{1:t-1}, \tilde{\mathbf{a}}_{1:t-1})
  \]

### 可控性的关键设计

这篇工作的核心不是单纯“视频更清晰”，而是“动作是否真的控制了生成结果”。为此论文强调：

- 动作不是人工标签，而是模型自发现的离散潜在代码。
- 潜在动作空间被压缩到较小码本中，便于控制和复用。
- 动作以条件信息进入动态模型，从而把视频续写变成类似“状态 + 动作 -> 下一状态”的[[世界模型]]问题。

论文用如下指标衡量可控性：

\[
\Delta_t \mathrm{PSNR} = \mathrm{PSNR}(x_t, \hat{x}_t) - \mathrm{PSNR}(x_t, \hat{x}'_t)
\]

其中，一种生成使用从真实视频推断出的动作，另一种使用随机动作；如果前者更接近真实帧，说明动作变量确实在控制生成。

### 与以往工作的区别

相较于以往方法，[[Genie]] 的区别在于：

- 传统[[世界模型]]通常依赖动作标注；[[Genie]] 不需要真实控制信号。
- 一般[[视频生成]]模型常做弱控制，如文本或风格条件；[[Genie]] 追求逐帧交互控制。
- 相比更偏特定场景的 playable video generation 路线，[[Genie]] 强调规模化与更通用的基础[[世界模型]]方向。

## 实验与结论

### 主要实验结论

论文给出的结果主要有三点：

- **规模化有效**：动态模型从 40M 扩展到 2.7B，训练损失持续下降，说明该架构具备稳定的 scaling 趋势。
- **跨域可行**：在机器人数据上，2.5B 模型达到 FVD 82.7，表明方法不只适用于平台游戏视频。
- **可控性显著提升**：若使用 pixel-input 的 [[Genie]]，在一些设置下虽然 FVD 未必绝对最优，但 \(\Delta_t\mathrm{PSNR}\) 更高，说明控制信号更有效。

### 具体结果

在 [[Platformers]] 上：

- token-input 的 FVD 为 38.8
- pixel-input 的 FVD 为 40.1
- 但 \(\Delta_t\mathrm{PSNR}\) 从 1.33 提升到 1.91

在 [[Robotics]] 上：

- FVD 从 257.8 降到 136.4
- \(\Delta_t\mathrm{PSNR}\) 从 1.65 提升到 2.07

在 tokenizer 消融中：

- [[ST-ViViT]] 的 FVD 为 81.4
- [[ViT]] 为 114.5
- [[C-ViViT]] 为 272.7

这说明基于[[时空 Transformer]]的 tokenizer 在生成质量和可控性上都更有优势。

### 论文结论

论文的总体结论是：只靠无标注视频，模型也可以学出具有一定一致性和可操作性的潜在动作空间，并进一步构建可交互的生成环境。这使[[Genie]]成为“基础[[世界模型]]”方向上的早期代表之一。

## 限制与待解

- 潜在动作并不是人类可直接解释的真实动作标签，更像抽象按键，语义映射不总是稳定。
- 训练数据主要来自 2D 平台游戏，学到的交互规律带有明显领域偏置，向复杂 3D 开放世界迁移并不容易。
- 模型规模大、训练依赖高算力与大量视频，复现门槛高。
- 评测虽包含 FVD 和 \(\Delta_t\mathrm{PSNR}\)，但对长期可玩性、因果一致性、任务可解性等更接近“环境质量”的指标，验证仍不系统。

## 历史位置

[[Genie]] 直接建立在几条技术线上：

- [[Vision Transformer]] 与[[时空 Transformer]]的视频建模路线
- [[VQ-VAE]] 的离散表示学习
- [[MaskGIT]] 一类 token-based 生成解码方法
- [[世界模型]]与 playable video generation 的研究传统

它的重要意义在于：把“生成内容”进一步推进到了“生成可交互环境”。从研究脉络看，这篇工作像是从[[视频生成]]走向基础[[世界模型]]、再走向通用智能体训练环境的一次范式转移信号。

## 涉及概念

- [[Genie]]
- [[世界模型]]
- [[生成式交互环境]]
- [[视频生成]]
- [[潜在动作模型]]
- [[LAM]]
- [[时空 Transformer]]
- [[ST-Transformer]]
- [[ST-ViViT]]
- [[VQ-VAE]]
- [[MaskGIT]]
- [[视频 tokenizer]]
- [[Video Tokenizer]]
- [[自回归动态建模]]
- [[Dynamics Model]]
- [[World Model]]
- [[无监督学习]]
- [[可控视频生成]]
- [[基础世界模型]]
- [[Vision Transformer]]
- [[ViT]]
- [[C-ViViT]]
- [[Platformers]]
- [[Robotics]]
