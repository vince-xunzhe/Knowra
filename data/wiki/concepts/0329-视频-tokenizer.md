---
kind: "concept"
title: "视频 tokenizer"
aliases:
  - "concept:329"
  - "视频-tokenizer"
  - "视频 tokenizer"
concept_id: 329
slug: "视频-tokenizer"
node_type: "technique"
concept_origin: "auto"
tags:
  - "视频生成"
  - "Video Tokenizer"
  - "潜在动作模型"
  - "时空 Transformer"
  - "ST-ViViT tokenizer"
  - "VQ-VAE"
  - "基础世界模型"
  - "世界模型"
  - "无监督学习"
  - "生成式交互环境"
  - "MaskGIT"
  - "可控视频生成"
source_paper_ids:
  - 17
compiled_at: "2026-05-13T11:49:52.583245+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "babfd0dcc45157f0432df3a6cb4314c94d79875b"
---

# 视频 tokenizer

## 定义

视频 tokenizer 是 Genie 中用于把视频压缩成离散表示的模块，也可以理解为一种视频压缩编码器。它将原始视频帧转换为离散 token，使后续模型不必直接在像素空间中建模，而是在更紧凑的符号空间中预测视频变化 [[paper:17]]。

在 Genie 的整体流程里，video tokenizer 是第一步：输入一段视频后，它基于 VQ-VAE 和时空 Transformer，把每帧压缩为离散 token，同时保留画面内容和时间变化信息 [[paper:17]]。这些 token 随后被用于两个关键模块：潜在动作模型从相邻帧变化中推断离散动作，动态模型则根据历史视频 token 和动作预测下一帧 token，再解码回图像 [[paper:17]]。

## 作用

视频 tokenizer 的核心作用不是单纯“压缩视频”，而是为可交互世界模型提供一个可建模、可预测的中间表示。相比直接预测像素，离散 token 让模型可以在更抽象的空间中学习视频动态，也让后续的 MaskGIT 式自回归预测器能够逐帧生成未来画面 [[paper:17]]。

在 Genie 中，这种 token 化还服务于控制：模型先把画面变化表示成 token，再让潜在动作模型解释相邻帧之间的变化。也就是说，视频 tokenizer 提供了“世界状态”的离散表示，而潜在动作模型学习“状态变化”的离散表示，两者共同构成可交互生成环境的基础 [[paper:17]]。

## 设计要点

材料中强调，Genie 的 tokenizer 采用 VQ-VAE 加时空 Transformer 的形式，并比较了不同 tokenizer 结构。消融结果显示，ST-ViViT 的表现最好：其 FVD 为 81.4，优于 ViT 的 114.5 和 C-ViViT 的 272.7，同时可控性指标也最高 [[paper:17]]。

这说明，对视频 tokenizer 来说，仅做逐帧图像压缩并不够；显式建模时空结构对于视频生成质量和后续可控性都很重要 [[paper:17]]。在 Genie 的设定中，tokenizer 既要保留视觉细节，又要使时间变化在离散空间中可被动态模型学习。

## 小结

在 Genie 中，视频 tokenizer 是把无标注视频转化为可学习世界模型的基础接口。它把连续视频压缩成离散 token，使模型能够在 token 空间中学习潜在动作和动态预测。现有材料主要展示了它在 Genie 架构中的实用价值，尤其是时空 Transformer tokenizer 对生成质量和可控性的贡献 [[paper:17]]。
