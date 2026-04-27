---
kind: "concept"
title: "Waymo Open Dataset"
concept_id: 232
slug: "waymo-open-dataset"
node_type: "dataset"
tags:
  - "DriveLM-Data"
  - "Graph Visual Question Answering"
  - "nuScenes"
  - "CARLA"
  - "DriveLM-Agent"
  - "End-to-end Autonomous Driving"
  - "LoRA"
  - "trajectory tokenization"
  - "graph prompting"
  - "Vision-Language Model"
source_paper_ids:
  - 14
compiled_at: "2026-04-26T16:52:53.001965+00:00"
compile_model: "gpt-4o-mini"
---

# Waymo Open Dataset

## Waymo Open Dataset

Waymo Open Dataset 是一个用于零样本跨传感器配置评测的数据集，旨在推动自动驾驶技术的发展。该数据集包含丰富的驾驶场景数据，支持多种感知和决策任务的研究。

## 不同视角

在相关研究中，Waymo Open Dataset 被用于构建和验证新的自动驾驶模型。例如，论文《DriveLM: Driving with Graph Visual Question Answering》提出了一种基于图的视觉问答模型 DriveLM，该模型利用 Waymo Open Dataset 中的数据进行训练和评估。DriveLM 通过将驾驶场景转化为图结构，允许模型在多个阶段进行感知、预测和规划，从而提高自动驾驶的安全性和效率。

## 共识与分歧

研究者们普遍认为，Waymo Open Dataset 为自动驾驶领域提供了一个重要的基准，尤其是在零样本学习和跨传感器配置的评测方面。然而，关于如何最有效地利用该数据集进行模型训练和评估，仍存在一些分歧。例如，DriveLM 的提出者强调了图结构在处理复杂驾驶场景中的优势，而其他研究可能更关注于传统的深度学习方法在数据集上的表现。

## 进一步阅读

对于想深入了解 Waymo Open Dataset 的研究者，可以参考《DriveLM: Driving with Graph Visual Question Answering》一文，该文详细介绍了如何利用该数据集构建和评估自动驾驶模型，并提出了相应的评测指标和方法。
