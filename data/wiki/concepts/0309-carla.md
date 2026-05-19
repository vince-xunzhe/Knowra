---
kind: "concept"
title: "CARLA"
aliases:
  - "concept:309"
  - "carla"
  - "CARLA"
concept_id: 309
slug: "carla"
node_type: "dataset"
concept_origin: "auto"
tags:
  - "BLIP-2"
  - "多步推理"
  - "端到端自动驾驶"
  - "轨迹预测"
  - "Graph Visual Question Answering"
  - "LoRA"
  - "零样本泛化"
  - "驾驶行为预测"
  - "轨迹离散化"
  - "Vision Language Model"
source_paper_ids:
  - 14
compiled_at: "2026-05-13T11:48:34.046243+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "f2787e51da90d96b3b4d85d7c2d17667ce5395d2"
---

# CARLA

## 定义

CARLA 在这篇材料中指 DriveLM 数据体系中的仿真数据部分，即 **DriveLM-CARLA**：基于 CARLA 仿真环境生成的自动驾驶图结构问答数据，用于训练和评估模型在驾驶场景中的感知、预测、规划与行为推理能力 [[paper:14]]。

它不是单纯的图像或轨迹数据集，而是被组织成 **Graph VQA** 形式：每一帧场景围绕驾驶决策拆成多个有依赖关系的问答节点，例如先识别关键目标，再预测目标行为，随后判断自车可采取的安全动作，最后汇总为驾驶行为并辅助生成轨迹 [[paper:14]]。

## 主要作用

在 DriveLM 中，CARLA 仿真数据的价值主要体现在三点：

1. **补充可控驾驶场景**：CARLA 作为仿真环境，可以提供与真实数据 nuScenes 不同的训练和测试来源，用于考察模型是否只记住特定传感器或数据分布。
2. **提供结构化语言监督**：DriveLM-CARLA 平均每帧约 24.4 个 QA，覆盖感知、预测和规划，并以图结构组织，而不是只给出单一驾驶指令或轨迹标签 [[paper:14]]。
3. **服务于可解释驾驶推理**：模型需要沿着“看见什么—它们会怎么动—我该怎么开”的链路回答问题，因此 CARLA 数据不只是训练轨迹预测，也训练中间推理过程 [[paper:14]]。

## 与 DriveLM 框架的关系

DriveLM 使用 CARLA 数据并不是为了单独提出一个新的仿真 benchmark，而是把它纳入 DriveLM-Data，用来支持 Graph VQA 驾驶任务。其核心目标是让视觉语言模型在自动驾驶中具备更自然的分步推理能力：先回答图结构问题，再生成驾驶行为描述，最后转化为未来轨迹 [[paper:14]]。

因此，CARLA 在这里更像是 **仿真场景下的推理监督来源**，而不是传统意义上只用于感知检测或端到端控制的数据集。它的重点在于多阶段问答标注和驾驶决策逻辑，而非单独的传感器数据规模或仿真物理细节。

## 进一步阅读

可阅读 DriveLM: Driving with Graph Visual Question Answering，重点关注其中的 DriveLM-Data、DriveLM-CARLA 和 Graph VQA 设计 [[paper:14]]。
