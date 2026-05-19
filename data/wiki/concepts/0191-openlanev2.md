---
kind: "concept"
title: "OpenLaneV2"
aliases:
  - "concept:191"
  - "openlanev2"
  - "OpenLaneV2"
concept_id: 191
slug: "openlanev2"
node_type: "dataset"
concept_origin: "auto"
tags:
  - "端到端自动驾驶"
  - "多任务学习"
  - "Vision-Language-Action"
  - "统一解码器"
  - "Vision-Language Model"
  - "LoRA"
  - "3D目标检测"
  - "因果注意力"
  - "轨迹规划"
  - "车道线检测"
source_paper_ids:
  - 24
compiled_at: "2026-05-13T11:44:47.142756+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "3e9b2f048b85e70ce035a1b3c8c0bbc92b784c2b"
---

# OpenLaneV2

## 定义

OpenLaneV2 是 OneDrive 中用于车道相关监督的标注来源，即为模型的车道结构预测任务提供训练信号的数据集 [[paper:24]]。

在 OneDrive 的统一自动驾驶框架里，车道预测不是单独由一个独立车道检测模块完成，而是通过“车道查询 token”与图像 token、检测查询 token、规划查询 token 和文本 token 一起输入同一个 Transformer 解码器。OpenLaneV2 在这里承担的是车道监督数据来源的角色，帮助模型学习如何从环视图像中预测车道结构 [[paper:24]]。

## 在 OneDrive 中的作用

OneDrive 的核心目标是把感知、规划和文本生成统一到同一个解码器中。车道任务是其中的结构化感知任务之一：模型准备专门的车道查询，用共享注意力骨干从图像 token 中获取视觉条件信息，再输出车道结构预测 [[paper:24]]。

因此，OpenLaneV2 在该论文中的意义不在于提出新的数据集方法，而在于作为车道监督标注来源，支持 OneDrive 验证其“统一解码器同时处理检测、车道、规划和文本”的框架设计 [[paper:24]]。

## 小结

从现有材料看，OpenLaneV2 可被理解为 OneDrive 训练车道结构预测能力时使用的监督数据来源。它服务于论文中统一多任务自动驾驶框架的车道分支，使车道预测能够与检测、规划和文本生成共享同一套预训练视觉语言模型注意力骨干 [[paper:24]]。
