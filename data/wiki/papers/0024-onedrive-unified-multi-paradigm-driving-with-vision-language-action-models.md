---
kind: "paper"
title: "OneDrive: Unified Multi-Paradigm Driving with Vision-Language-Action Models"
aliases:
  - "paper:24"
  - "OneDrive: Unified Multi-Paradigm Driving with Vision-Language-Action Models"
  - "onedrive-unified-multi-paradigm-driving-with-vision-language-action-models"
paper_id: 24
slug: "onedrive-unified-multi-paradigm-driving-with-vision-language-action-models"
authors:
  - "Yiwei Zhang"
  - "Xuesong Chen"
  - "Jin Gao"
  - "Hanshi Wang"
  - "Fudong Ge"
  - "Weiming Hu"
  - "Shaoshuai Shi"
  - "Zhipeng Zhang"
paper_category: "VLA"
compiled_at: "2026-05-07T13:26:56.997812+00:00"
compile_model: "gpt-5.4"
source_signature: "08968cee7af0788133688da6d6cb1b04e8ee9a89"
source_record: "data/paper_records/0024-2604.17915v1.md"
---

# OneDrive: Unified Multi-Paradigm Driving with Vision-Language-Action Models

## 一句话定位

[[OneDrive]] 是一个面向[[自动驾驶]]的统一 [[Vision-Language-Action]] 框架：它将图像 token、结构化查询 token（检测/车道/规划）和文本 token 拼成同一序列，交给单个[[Transformer]]因果解码器处理，从而在一个共享骨干中同时完成感知、规划与文本生成。

## 核心贡献

- 提出单解码器统一范式，不再为感知、规划、文本分别设计多个解码器，而是把多种输出范式统一到同一个因果解码流程中。
- 证明预训练 [[Vision-Language Model]] 的注意力层比前馈层更适合迁移到驾驶任务，因此保留共享注意力骨干，只为结构化任务补充少量专用模块。
- 在统一框架中同时支持：
  - [[3D目标检测]]
  - [[车道线检测]]
  - [[轨迹规划]]
  - 文本生成
- 在保持文本能力的同时，兼顾精度与效率：相对多解码器/级联系统，推理延迟显著下降，并在开环与闭环驾驶评测上取得更优结果。

## 方法

### 整体思路

[[OneDrive]] 的核心是把“统一”落实在解码层。输入首先是环视相机图像，可选附加文本提示。图像经过编码后得到 image tokens；同时构造三类结构化查询：

- 检测查询：用于[[3D目标检测]]
- 车道查询：用于[[车道线检测]]
- 规划查询：用于[[轨迹规划]]
- 文本 token：保留原有语言生成接口

之后将所有 token 按顺序拼接为统一序列：

\[
Z = [X_{img}, Q_{det}, Q_{lane}, Q_{plan}, X_{text}]
\]

这意味着后续的结构化任务与文本任务都在同一个序列建模过程中完成。

### 统一因果解码器

模型使用预训练 [[Vision-Language Model]] / [[Transformer]] 的因果自注意力作为共享骨干。由于序列顺序是 `[图像, 检测, 车道, 规划, 文本]`，后面的查询 token 可以自然访问前面的图像 token，从而获得视觉条件信息，而无需为每个任务单独设计 cross-attention 解码器。

论文将这种机制视为统一多任务输出的关键：视觉、结构化预测、语言生成共享同一套“关系建模”能力。

### 空间建模

对于图像 token 和结构化查询 token，论文在注意力投影中加入[[RoPE]]与 3D 位置信息：

\[
Q = \mathrm{RoPE}(XW_q) + e_{3D},\quad K = \mathrm{RoPE}(XW_k) + e_{3D}
\]

其作用是增强模型对空间关系的理解，使共享注意力骨干更适合驾驶场景中的几何推理。

### 感知查询的额外自注意力

单纯的因果注意力更适合顺序生成，不一定最适合并行结构化预测。为此，论文在浅层额外加入感知查询内部的自注意力，让检测查询和车道查询彼此交互：

\[
Q_{perception} = \mathrm{SelfAttn}_q([Q_{det}, Q_{lane}])
\]

这一步专门用于改善感知任务中的并行预测能力。

### 任务专用前馈层

作者发现，预训练模型中的注意力层较易迁移，但原本偏文本的 FFN 不完全适合结构化驾驶任务。因此，不同任务采用专用 FFN：

\[
Q' = \mathrm{FFN}_t(\tilde{Q}),\quad t \in \{det, lane, plan\}
\]

也就是说，模型共享注意力骨干，但在特征变换阶段为[[3D目标检测]]、[[车道线检测]]、[[轨迹规划]]分别做轻量适配。

### 方法直觉

这个框架可以理解为把原本分散的多个“部门”拉进同一个会议室：图像信息先进入共享上下文，检测、车道、规划和文本都沿着同一套注意力流程读取与更新表示。这样做减少了多分支系统中的信息割裂，也避免把连续轨迹、并行检测等任务硬编码成纯文本生成。

## 实验与结论

### 开环规划

在 [[nuScenes]] 开环规划评测上，[[OneDrive]] 达到：

- 1s / 2s / 3s 的 L2 误差：0.13 / 0.25 / 0.46
- 平均 L2：0.28
- 1s / 2s / 3s 的碰撞率：0.00 / 0.12 / 0.43
- 平均碰撞率：0.18%

与基线相比：

- [[SOLVE-E2E]]：0.31 L2、0.30% 碰撞率
- [[ColaVLA]]：0.30 L2、0.23% 碰撞率

论文指出，[[OneDrive]] 在精度和安全性上都优于这些强基线；相较 [[ColaVLA]]，平均碰撞率下降约 23%。

### 闭环评测

在 [[NAVSIM]] 闭环评测上，[[OneDrive]] 取得：

- 86.8 [[PDMS]]

高于：

- Query Decoder baseline：85.0
- [[ReCogDrive]](SFT)：86.5

### 效率

在 [[NAVSIM]] 上，单帧延迟从 [[ReCogDrive]] 的 263 ms 降至 156 ms，约减少 40%。这说明单解码器统一设计不只是结构更简洁，也在实际推理效率上带来收益。

### 结论

论文的实验结论很明确：共享单一因果解码器不仅能统一多种驾驶输出范式，还能在开环规划、闭环驾驶和实时性上同时取得竞争力结果。其价值不在于发明全新基础模块，而在于证明预训练 [[Vision-Language Model]] 的解码骨干可以通过最小改动适配到自动驾驶中的感知、规划与语言共存场景。

## 限制与待解

- 方法依赖高质量预训练 [[Vision-Language Model]]；若底座模型能力不足，共享骨干的收益可能受限。
- 虽然比一些 VLM 驾驶方案更快，但多视角图像与多类查询共同带来较长 token 序列，真实车端部署仍有算力压力。
- 主要验证数据集是 [[nuScenes]] 和 [[NAVSIM]]，对极端天气、稀有交通事件等长尾场景的鲁棒性尚未充分证明。
- 结构上仍需任务专用 FFN 和浅层额外注意力，说明“完全无改动统一”尚未实现。
- 复现可能依赖较细致的训练策略设计，如模块冻结、阶段划分和损失权重设置。

## 涉及概念

- [[自动驾驶]]
- [[端到端自动驾驶]]
- [[Vision-Language-Action]]
- [[Vision-Language Model]]
- [[Transformer]]
- [[因果注意力]]
- [[RoPE]]
- [[多任务学习]]
- [[统一解码器]]
- [[统一查询]]
- [[3D目标检测]]
- [[车道线检测]]
- [[轨迹规划]]
- [[LoRA]]
- [[nuScenes]]
- [[NAVSIM]]
- [[InternVL3]]
- [[StreamPETR]]
- [[VAD]]
- [[SOLVE-E2E]]
- [[ColaVLA]]
- [[ReCogDrive]]
