---
kind: "concept"
title: "提高PushT成功率"
concept_id: 114
slug: "提高pusht成功率"
node_type: "finding"
tags:
  - "世界模型"
  - "潜在表示"
  - "强化学习"
  - "自监督学习"
  - "视觉变换器"
  - "反崩溃"
  - "策略优化"
  - "像素输入"
  - "动态模拟"
  - "基于模型控制"
source_paper_ids:
  - 10
compiled_at: "2026-04-26T16:33:50.143516+00:00"
compile_model: "gpt-4o-mini"
---

# 提高PushT成功率

## 提高PushT成功率

在PushT任务中，LeWM（LeWorldModel）的成功率达到了96%，相比于PLDM（Predictive Latent Dynamics Model）提高了18%。这一成果表明，LeWM在处理复杂任务时展现出了更强的能力。

## 定义

LeWM模型的核心思想是通过观察环境来预测未来状态，类似于一个经验丰富的司机在驾驶过程中对交通状况的判断。该模型通过编码器将输入数据转化为低维潜在表示，随后利用预测器基于当前状态和动作预测下一个状态。这一过程通过均方误差损失进行优化，并采用SIGReg正则化技术，以促进特征的多样性，避免模型崩溃。

## 共识与分歧

在现有研究中，LeWM的成功率显著高于PLDM，显示出其在任务执行中的优势。研究者们普遍认为，LeWM的架构能够简化训练流程、实现高效规划并提升模型的稳健性。然而，尽管LeWM在成功率上表现优异，仍需进一步探讨其在不同环境和任务复杂度下的适应性和泛化能力。

## 进一步阅读

有关LeWM的详细架构和性能分析，可以参考论文《LeWorldModel: End-to-End Learning of Latent World Models from Pixels》[[paper:10]]。
