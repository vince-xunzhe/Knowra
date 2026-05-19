---
kind: "concept"
title: "nuPlan"
aliases:
  - "concept:194"
  - "nuplan"
  - "nuPlan"
concept_id: 194
slug: "nuplan"
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
compiled_at: "2026-05-13T11:45:09.070886+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "953c11814e0e86e84940f91b36a6638f9b86ddcb"
---

# nuPlan

## 定义

nuPlan 在这里被提及为 NAVSIM 的上游数据来源，是自动驾驶闭环评测相关数据链条中的基础数据集之一。材料中没有展开 nuPlan 本身的采集规模、传感器配置或标注细节，因此只能将其定位为 NAVSIM 基准背后的数据来源，而不是独立分析其数据结构。

## 在相关研究中的作用

在 OneDrive 中，nuPlan 并不是直接作为主要实验名称出现，而是通过 NAVSIM 闭环评测间接关联。论文报告 OneDrive 在 NAVSIM navtest 上达到 86.8 PDMS，说明该模型不仅在 nuScenes 开环规划上有效，也能在以 nuPlan 为上游来源的 NAVSIM 闭环设置中取得较强表现 [[paper:24]]。

从这个角度看，nuPlan 的意义主要体现在：它支撑了更接近闭环驾驶评估的数据与场景基础，使研究者能够考察模型在规划决策链条中的综合表现，而不只是单步轨迹误差。OneDrive 在 NAVSIM 上的结果被用来证明其统一视觉-语言-动作框架具备闭环规划能力 [[paper:24]]。

## 进一步阅读

可从 OneDrive 的 NAVSIM 实验部分入手，理解 nuPlan 作为 NAVSIM 上游数据来源时，在自动驾驶闭环评测中的间接角色 [[paper:24]]。
