---
kind: "paper"
title: "Qwen3-VL-Seg: Unlocking Open-World Referring Segmentation with Vision-Language Grounding"
aliases:
  - "paper:26"
  - "Qwen3-VL-Seg: Unlocking Open-World Referring Segmentation with Vision-Language Grounding"
  - "qwen3-vl-seg-unlocking-open-world-referring-segmentation-with-vision-language-grounding"
paper_id: 26
slug: "qwen3-vl-seg-unlocking-open-world-referring-segmentation-with-vision-language-grounding"
authors:
  - "Yuan Yao"
  - "Qiushi Yang"
  - "Humen Zhong"
  - "Jiangning Wei"
  - "Yifang Men"
  - "Shuai Bai"
  - "Miaomiao Cui"
  - "Zhibo Yang"
paper_category: "VLM"
compiled_at: "2026-05-13T09:40:36.626158+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "626bc232d1a5836c8ebcd7fc7f388ea381cb06d9"
source_record: "data/paper_records/0026-2605.07141v1.md"
---

# Qwen3-VL-Seg: Unlocking Open-World Referring Segmentation with Vision-Language Grounding

## 一句话定位

[[Qwen3-VL-Seg]] 是一个面向 [[open-world referring segmentation]] 的 [[Qwen3-VL]] 扩展：把多模态大模型已有的 [[vision-language grounding]] 和 bbox 预测能力，轻量转换为像素级分割能力。

## 核心贡献

这篇论文的核心贡献是：把 [[MLLM]] 会“画框”的能力，转成能“描边”的能力。

具体来说，[[Qwen3-VL-Seg]] 不依赖 [[SAM]] 作为推理阶段的外部分割器，而是在 [[Qwen3-VL]] 后接一个约 17M 参数的 [[box-guided mask decoder]]。模型先用 [[Qwen3-VL]] 理解图像和自然语言指代表达，预测目标 bbox，再把这个 bbox 作为结构先验，引导轻量 decoder 生成精细 mask。

论文还构建了两个数据资源：

- [[SA1B-ORS]]：用于开放世界指代分割训练。
- [[ORS-Bench]]：用于评测开放词汇、复杂描述和 OOD 场景下的分割能力。

## 方法

[[Qwen3-VL-Seg]] 的整体流程可以理解为“先圈重点，再沿边界抠图”。

输入是一张图像和一段自然语言指代表达。[[Qwen3-VL]] 首先编码图像与文本，输出多尺度视觉特征、语言对齐的视觉 embedding、`<Seg>` token 特征以及目标 bbox。随后，轻量 [[box-guided mask decoder]] 使用这些信息生成最终 mask。

方法中的关键设计包括：

1. [[空间特征注入]]

   模型在中间 [[ViT]] 特征上加入轻量局部卷积分支，让高层语义特征补充边界和局部纹理信息。对应公式为：

   ```text
   F̃^l = X_0^(l) + s · GELU(DWConv(GroupNorm(X_0^(l))))
   ```

   其作用是用很小的局部建模成本增强边界感知能力。

2. [[空间语义查询]]

   模型把 bbox 的几何信息和 `<Seg>` token 的语义信息融合成 object query：

   ```text
   Q_seg^(0) = LayerNorm(MLP_box(E_box) + W_seg T_seg)
   ```

   这个 query 同时知道“要找谁”和“大概在哪里”。

3. [[框引导软门控]]

   模型根据放大的 bbox 生成软空间权重图：

   ```text
   M(x,y)=σ(α(x-x'_1))·σ(α(x'_2-x))·σ(α(y-y'_1))·σ(α(y'_2-y))
   ```

   这样浅层 CNN 的高清纹理主要从目标附近进入 decoder，减少背景干扰，同时保留一定容错空间。

4. [[掩码感知迭代细化]]

   decoder 先预测一版粗 mask，再用该 mask 对目标区域像素特征做 soft pooling，并把聚合得到的目标证据反馈给 query，生成第二轮细化 mask：

   ```text
   F_tar = Σ(σ(M_logit^(1)) ⊙ F_pixel) / (Σσ(M_logit^(1)) + ε)
   Q_seg^(2) = LayerNorm(Q_seg^(1) + φ_ref(F_tar))
   M_logit^(2) = Ψ(Q_seg^(2), F_pixel)
   ```

   这一步让模型能根据第一轮 mask 的结果修正边界和区域选择。

## 实验与结论

论文报告 [[Qwen3-VL-Seg]] 在 closed-set 与 open-world [[referring segmentation]]、[[visual grounding]] 和 [[ORS-Bench]] 上整体表现强。

最突出的结果来自开放世界复杂指令场景：

- 在 category instructions 上，相比最强 baseline 提升 19.0 cIoU 和 13.6 P@0.5。
- 在 descriptive instructions 上，相比最强 baseline 提升 15.5 cIoU 和 13.1 P@0.5。

定性结果显示，[[Gemini]]、[[Seed]]、[[SAM3]] 等方法在复杂场景中容易出现漏分、错分或边界粗糙；[[Qwen3-VL-Seg]] 在多实例类别、短语指代和描述性指令中更稳定。

## 历史位置

[[Qwen3-VL-Seg]] 建立在 [[Qwen-VL]] / [[Qwen3-VL]] 的开放词汇视觉 grounding 能力之上，也延续了 [[RefCOCO]] 系列、[[GRES]]、[[ReasonSeg]] 等语言指代分割任务设定，并利用 [[SA-1B]] 的大规模 mask 资源。

相比 [[LISA]]、[[GSVA]]、[[SAM4MLLM]] 这类串接 [[MLLM]] 与外部 [[SAM]] 分割器的方法，[[Qwen3-VL-Seg]] 更强调 [[SAM-free segmentation]]：用更轻的 decoder 直接把 bbox grounding 转成 mask prediction。

相比 [[Text4Seg]]、[[UFO]]、[[MLLMSeg]] 这类不依赖 [[SAM]] 的方法，本文的关键区别是显式把 [[MLLM]] 预测 bbox 当作结构先验，并在 query 构造、高清像素融合和迭代细化中反复使用。

## 限制与待解

[[Qwen3-VL-Seg]] 的主要限制包括：

1. 强依赖 [[Qwen3-VL]] 的 bbox 质量。如果初始框错了或漏了，后续 mask decoder 很难完全纠正。
2. [[SA1B-ORS]] 的构建依赖 [[Qwen3-VL-Plus]]、[[SAM2]]、[[RAM++]] 和自动验证流程，复现成本较高，也可能继承这些模型的偏差。
3. 虽然新增参数只有 17M，但模型仍需要访问多尺度特征、浅层 CNN 特征和两轮 mask refinement，部署复杂度高于纯 bbox 输出。
4. [[ORS-OOD-Bench]] 每个维度约 200 个挑战样本，能够暴露问题，但未必覆盖真实开放世界中的全部长尾风险。

## 涉及概念

- [[Qwen3-VL-Seg]]
- [[Qwen3-VL]]
- [[Qwen-VL]]
- [[open-world referring segmentation]]
- [[referring segmentation]]
- [[vision-language grounding]]
- [[MLLM]]
- [[box-guided mask decoder]]
- [[SAM-free segmentation]]
- [[pixel-level mask prediction]]
- [[SA1B-ORS]]
- [[ORS-Bench]]
- [[ORS-OOD-Bench]]
- [[SA-1B]]
- [[SAM]]
- [[SAM2]]
- [[SAM3]]
- [[LISA]]
- [[GSVA]]
- [[SAM4MLLM]]
- [[Text4Seg]]
- [[UFO]]
- [[MLLMSeg]]
- [[RefCOCO]]
- [[GRES]]
- [[ReasonSeg]]
- [[RAM++]]
