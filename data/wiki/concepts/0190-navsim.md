---
kind: "concept"
title: "NAVSIM"
aliases:
  - "concept:190"
  - "navsim"
  - "NAVSIM"
concept_id: 190
slug: "navsim"
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
compiled_at: "2026-05-13T11:44:23.322129+00:00"
compile_model: "codex-cli/gpt-5.5"
source_signature: "b6612bca617cdec73c44d0bfff13a925d5ce3057"
---

# NAVSIM

## 定义

NAVSIM 在这里作为自动驾驶模型的闭环评测数据集/基准出现，用于衡量模型在模拟闭环驾驶中的规划能力。材料中明确提到的指标是 NAVSIM navtest 上的 PDMS 分数，OneDrive 在该基准上达到 86.8 PDMS [[paper:24]]。

## 在 OneDrive 中的作用

在 OneDrive 论文中，NAVSIM 主要用于验证统一视觉-语言-动作模型是否不仅能在 nuScenes 开环规划中表现良好，也能迁移到闭环驾驶评测。OneDrive 将图像 token、检测查询、车道查询、规划查询和文本 token 拼接为统一序列，由同一个 Transformer 解码器处理；在 NAVSIM navtest 上取得 86.8 PDMS，高于 Query Decoder baseline 的 85.0，也略高于 ReCogDrive(SFT) 的 86.5 [[paper:24]]。

## 可见结论

从这篇材料看，NAVSIM 被用来支撑一个核心判断：统一解码器框架不仅改善开环轨迹误差和碰撞率，也能在闭环指标上保持竞争力。它在论文中的角色不是被详细介绍的数据集对象，而是作为闭环评测场景，证明 OneDrive 的规划能力在更接近交互式驾驶的评估中仍有提升 [[paper:24]]。
