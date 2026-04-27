---
kind: "paper"
title: "SpaceDrive: Infusing Spatial Awareness into VLM-based Autonomous Driving"
paper_id: 2
slug: "spacedrive-infusing-spatial-awareness-into-vlm-based-autonomous-driving"
authors:
  - "Peizheng Li"
  - "Zhenghao Zhang"
  - "David Holtz"
  - "Hang Yu"
  - "Yutong Yang"
  - "Yuzhi Lai"
  - "Rui Song"
  - "Andreas Geiger"
  - "Andreas Zell"
compiled_at: "2026-04-26T16:16:07.952113+00:00"
compile_model: "gpt-4o-mini"
source_record: "data/paper_records/0002-2512.10719v1.md"
---

# SpaceDrive: Infusing Spatial Awareness into VLM-based Autonomous Driving

## 一句话定位
SpaceDrive 是一个具有空间意识的框架，通过引入统一的3D坐标编码来提升基于视语言模型（VLM）的端到端自动驾驶的规划能力。

## 核心贡献
通过引入统一的3D坐标编码，解决了当前VLM对3D空间关系理解的限制，显著提升了端到端自动驾驶的规划能力。

## 方法
SpaceDrive 的架构流程如下：
1. 视觉编码器对周围环境进行图像编码。
2. 深度估算器计算每个图像的绝对深度，并通过通用的[[PE编码器]]将其转换为3D位置编码，增强视觉标记。
3. 通过语言模型进行推理，输出阶段使用[[PE解码器]]生成精确的3D坐标用于规划。

关键公式包括：
- **式(1) 视觉编码**：将多视角图像转换为补丁标记。
- **式(2) 特征对齐**：通过简单MLP对齐视觉和语言特征空间。
- **式(3) 空间位置编码**：将3D坐标转换为位置编码，以加强视觉标记。
- **式(8) 损失函数**：结合语言建模和坐标回归损失优化模型。

## 实验与结论
SpaceDrive 在[[nuScenes]]数据集上实现了开放环路中的领先表现，得分78.02。在[[Bench2Drive]]封闭环路基准测试中取得了次佳的驾驶成绩，显示出其在动态复杂场景中的合理规划能力。

## 限制与待解
当前方法对不确定性的处理较为有限，且没有利用多帧时间记忆机制，这可能限制在动态长序列预测中的性能。此外，模型对多样化数据的适应性会影响实际场景部署时的鲁棒性。

## 涉及概念
- [[VLM]]（视语言模型）
- [[3D位置编码]]
- [[轨迹规划]]
