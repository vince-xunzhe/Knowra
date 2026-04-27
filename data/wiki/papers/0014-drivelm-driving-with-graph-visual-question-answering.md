---
kind: "paper"
title: "DriveLM: Driving with Graph Visual Question Answering"
paper_id: 14
slug: "drivelm-driving-with-graph-visual-question-answering"
authors:
  - "Chonghao Sima"
  - "Katrin Renz"
  - "Kashyap Chitta"
  - "Li Chen"
  - "Hanxue Zhang"
  - "Chengen Xie"
  - "Jens Beißwenger"
  - "Ping Luo"
  - "Andreas Geiger"
  - "Hongyang Li"
compiled_at: "2026-04-26T16:18:41.347554+00:00"
compile_model: "gpt-4o-mini"
source_record: "data/paper_records/0014-2312.14150v3.md"
---

# DriveLM: Driving with Graph Visual Question Answering

## 一句话定位
DriveLM 是一种通过图结构视觉问答实现的端到端自动驾驶智能体，旨在提高自动驾驶决策的可解释性和泛化能力。

## 核心贡献
本论文的核心贡献在于将自动驾驶决策拆解为图结构问答，使得视觉语言模型能够像人类一样先观察、再思考、最后规划。

## 方法
### Graph VQA
作者提出了 [[Graph Visual Question Answering]] (GVQA) 的框架，将感知、预测、规划、行为和轨迹等步骤组织成带有依赖关系的问答图。

### DriveLM-Agent
构建了 [[DriveLM-Agent]]，其架构基于 [[BLIP-2]]，通过以下阶段进行处理：
1. **感知阶段 (P1)**：识别关键物体、交通灯、车道等。
2. **预测阶段 (P2)**：推断物体的运动和交互。
3. **规划阶段 (P3)**：判断安全与危险动作。
4. **行为阶段 (B)**：汇总 P1-P3 的关键问答，生成自然语言驾驶行为。
5. **运动阶段 (M)**：使用 [[轨迹分词]] 将连续坐标离散成语言 token，输出未来的 waypoint。

### 关键公式
- **GVQA 图定义**：将一帧图像中的所有问答视为一个有向无环图 G=(V,E)。
- **问答节点定义**：每个节点 v=(q,a)，q 为问题，a 为答案。
- **运动轨迹定义**：未来轨迹 M 是一串鸟瞰图坐标点。
- **行为分类映射**：将相邻轨迹点之间的位移映射到速度和转向的离散类别。

## 实验与结论
在 [[DriveLM-nuScenes]] 的开环规划中，DriveLM-Agent 的性能优于传统模型，特别是在零样本设置下，展示了更好的泛化能力。实验结果显示，DriveLM-Agent 在多个指标上超越了基线模型。

## 限制与待解
DriveLM-Agent 继承了大语言模型推理速度慢的问题，且主要依赖低分辨率前视图像，限制了其空间理解和速度估计能力。此外，评测主要以开环为主，离真实闭环驾驶仍有差距。

## 涉及概念
- [[Vision-Language Model]] (VLM)
- [[Graph Visual Question Answering]] (GVQA)
- [[图提示]] (graph prompting)
- [[轨迹分词]] (trajectory tokenization)
- [[LoRA]] (Low-Rank Adaptation)
- [[DriveLM-nuScenes]]
- [[DriveLM-CARLA]]
- [[nuScenes]]
- [[CARLA]]
- [[Waymo Open Dataset]]
- [[COCO]]
- [[GQA]]
