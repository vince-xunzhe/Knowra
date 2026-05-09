---
kind: "paper"
title: "NuGrounding: A Multi-View 3D Visual Grounding Framework in Autonomous Driving"
paper_id: 12
slug: "nugrounding-a-multi-view-3d-visual-grounding-framework-in-autonomous-driving"
authors:
  - "Fuhao Li"
  - "Huan Jin"
  - "Bin Gao"
  - "Liaoyuan Fan"
  - "Lihui Jiang"
  - "Long Zeng"
paper_category: "VLA"
compiled_at: "2026-05-07T13:20:38.110115+00:00"
compile_model: "gpt-5.4"
source_signature: "049ebb32f245056a56557fcdf5f86928d7828a12"
source_record: "data/paper_records/0012-2503.22436v2.md"
---

# NuGrounding: A Multi-View 3D Visual Grounding Framework in Autonomous Driving

## 一句话定位

[[NuGrounding]] 是一个面向自动驾驶场景的多视角 3D 视觉指代框架：它将[[多模态大语言模型]]的语言理解能力与[[3D目标检测]]器的几何定位能力结合起来，用于根据复杂自然语言在多相机场景中精确定位 3D 目标。

## 核心贡献

- 提出了自动驾驶场景下的大规模多视角 3D 视觉指代数据集 [[NuGrounding]]。
- 提出一个解耦式框架：  
  - 用[[BEV]]检测器生成带几何先验的 [[object query]]；
  - 用[[MLLM]] 处理语言理解，并通过[[context query]]聚合语义与场景信息；
  - 用[[Fusion Decoder]] 融合语义与 3D 空间信息，输出 3D 边界框。
- 提出 [[HoG]] 分层构造方法，用类别、颜色、运动、相对方位等属性组合生成更丰富、更不易“走捷径”的指令。
- 在适配后的[[ELM]]、[[NuPrompt]]、[[OmniDrive]]等方法对比中，表现出明显优势。

## 方法

### 整体思路

论文将问题拆成“听懂指令”和“精确定位”两个部分：

- [[MLLM]] 负责理解语言描述中的语义，例如目标类别、颜色、位置关系等；
- [[BEV]]-based [[3D目标检测]]器负责从多视角图像中恢复场景几何，并提供候选实例；
- 再通过[[Fusion Decoder]]把语义理解与几何先验对齐，得到最终 3D 定位结果。

这个设计对应一种“语言模块负责说的是谁，检测模块负责具体在哪”的分工。

### 架构流程

输入是车辆周围六个相机的多视角图像和一段文本指令，整体流程分为三步：

#### 1. 检测器产生几何先验

[[BEV]]-based Detector 先将多视角图像编码为鸟瞰视角特征，再通过查询式检测解码器生成实例级 [[object query]]。这些 query 同时包含语义信息与 3D 几何先验。

#### 2. 语言模型聚合上下文

[[Context Query Aggregator]] 将 [[object query]] 通过适配器映射到[[大语言模型]]输入空间，并与文本 token 一起送入 [[MLLM]]。  
模型中额外引入两个解耦任务 token 和一个可学习的 [[context query]]。在生成文字回复的过程中，[[context query]] 持续吸收语言语义和场景几何信息。

#### 3. 融合解码并输出 3D 框

[[Fusion Decoder]] 先根据 [[context query]] 与 [[object query]] 的相似度筛除无关候选，再让选中的 query：
- 与全部 [[object query]] 交互，以补充空间细节；
- 与 [[context query]] 交互，以补充语义信息。

得到的 fused query 最终由检测头解码成 3D 边界框。

### 关键公式

#### 多模态输入拼接

\[
x_m = Concat([\gamma_1(Q_{obj}), x_{txt}])
\]

含义：先把检测器输出的 [[object query]] 通过两层适配器映射到[[MLLM]]可读空间，再与文本 token 拼接成统一输入。

#### 自回归响应生成

\[
x_a^{n+1} = F_{LLM}([x_m; x_a^1, x_a^2, \cdots, x_a^n])
\]

含义：[[LLM]] 基于多模态输入和已生成回复 token，逐步生成后续回复。

#### 检测标记解码

\[
[DET] = De(x_a^{n+1})
\]

含义：当生成到检测标记位置时，用 detokenizer 将该位置隐藏表示转成后续定位所需的检测信号。

#### 上下文查询聚合

\[
\tilde{Q}_{cont} = F_{LLM}([x_m; x_a^1, \cdots, x_a^n, Q_{cont}])
\]

含义：把可学习的 [[context query]] 纳入推理流程，使其在“看图、读话、生成回复”过程中逐步汇总跨模态信息。

#### 总损失

\[
L = w_{txt}L_{txt} + w_{det}L_{det} + w_cL_c
\]

含义：训练时联合优化文本生成、目标检测与查询选择三个目标。

### 数据构建思路

论文提出 [[HoG]] 方法来生成分层指令。其目标是让数据集中的语言描述兼顾：
- 多样性；
- 真实性；
- 不易让模型通过简单模式匹配“作弊”。

构造属性围绕类别、颜色、运动、相对方位等展开。

## 实验与结论

### 主要结果

论文将[[ELM]]、[[NuPrompt]]、[[OmniDrive]]等代表性方法适配到该任务上进行比较。结果显示，[[NuGrounding]] 即使使用较小的图像骨干 [[ViT-B]]，在四个层级平均上也达到：

- Precision: 0.59
- Recall: 0.64
- mAP: 0.40
- NDS: 0.48

相对 [[NuPrompt]] 分别领先：

- Precision +0.30
- Recall +0.35
- mAP +0.29
- NDS +0.26

作者还总结为：
- Precision 提升 50.8%
- Recall 提升 54.7%

### 消融结论

消融实验表明，解耦任务 token 与 [[context query]] 的设计是有效的：

- 仅使用“文本思考”基线时，已有 0.387 mAP 和 0.445 NDS；
- 加入 [[context query]] 后，又提升 0.056 mAP 和 0.042 NDS。

这说明 [[context query]] 在跨模态聚合中起到了明确作用。

### 定性现象

论文指出，在跨视角和遮挡场景下，该方法仍能更完整地找出符合描述的车辆，说明其在复杂多相机场景中的目标检索与定位更稳健。

## 限制与待解

- 强依赖专业[[3D检测器]]：如果检测器漏检候选目标，后续语言模块无法弥补。
- 数据集建立在 [[NuScenes]] 之上，属性设计主要围绕类别、颜色、运动和相对方位，更复杂的长尾描述、交互意图和时序变化覆盖仍有限。
- 框架由[[BEV]]编码器、[[MLLM]]、适配器、[[LoRA]]、[[Fusion Decoder]]等多模块组成，联训与调参成本较高，复现门槛不低。
- 通过生成文字回复辅助“思考”虽然有效，但可能带来推理延迟，对实时自动驾驶部署仍需优化。

## 涉及概念

- [[NuGrounding]]
- [[多视角3D视觉指代]]
- [[自动驾驶]]
- [[多模态大语言模型]]
- [[BEV]]
- [[3D目标检测]]
- [[视觉定位]]
- [[HoG]]
- [[上下文查询]]
- [[Fusion Decoder]]
- [[object query]]
- [[context query]]
- [[NuScenes]]
- [[ELM]]
- [[NuPrompt]]
- [[OmniDrive]]
- [[ViT-B]]
- [[LoRA]]
