---
kind: "paper"
title: "N3D-VLM: Native 3D Grounding Enables Accurate Spatial Reasoning in Vision-Language Models"
paper_id: 1
slug: "n3d-vlm-native-3d-grounding-enables-accurate-spatial-reasoning-in-vision-language-models"
authors:
  - "Yuxin Wang"
  - "Lei Ke"
  - "Boqiang Zhang"
  - "Tianyuan Qu"
  - "Hanxun Yu"
  - "Zhenpeng Huang"
  - "Meng Yu"
  - "Dan Xu"
  - "Dong Yu"
compiled_at: "2026-04-26T16:16:00.296653+00:00"
compile_model: "gpt-4o-mini"
source_record: "data/paper_records/0001-2512.16561v1.md"
---

# N3D-VLM: Native 3D Grounding Enables Accurate Spatial Reasoning in Vision-Language Models

## 一句话定位
N3D-VLM 是一个结合了 [[3D感知]] 和 [[空间推理]] 的统一框架，旨在提升 [[视觉语言模型]] 在3D锚定和空间理解任务中的表现。

## 核心贡献
提出了一种新的多模态框架，结合 [[3D感知]] 和 [[空间推理]]，实现精确的3D锚定和解释性空间理解。

## 方法
N3D-VLM 的工作流程如下：
1. 模型接收 [[RGB-D]] 图像作为输入。
2. 通过一个嵌入有本地3D感知能力的模块，模型识别并定位图像中的对象。
3. 利用定位结果进行 [[3D空间推理]]，如计算对象之间的距离和尺寸。
4. 输出包括精细的3D锚定和推理结果。

关键公式包括：
- **式(3) 欧氏距离计算**：用于计算3D空间中不同对象间的精确距离，通过 (x2 - x1)² + (y2 - y1)² + (z2 - z1)² 得到两点间的距离。
- **式(5) 3D锚定优化**：通过损失函数最小化预测3D位置和实际位置之间的误差，提高锚定精度。

## 实验与结论
在3D锚定和空间推理任务上，N3D-VLM 方法相较现有技术在多个基准上达到了最先进性能。例如，在 [[N3D-Bench]] 基准上，开放式问题回答准确率从原来的66%提高到89.7%。模型显示出更强大的空间推理能力，相较于现有系统实现了准确率的提升。

## 限制与待解
该模型在处理镜面反射及目标密集场景时有待改进，可能会误将反射识别为真实物体。此外，对计算资源及3D数据集的要求较高，增加了应用的成本和难度。

## 涉及概念
- [[3D感知]]
- [[空间推理]]
- [[视觉语言模型]]
- [[RGB-D]]
- [[N3D-Bench]]
