---
kind: "paper"
title: "OmniDrive: A Holistic Vision-Language Dataset for Autonomous Driving with Counterfactual Reasoning"
paper_id: 15
slug: "omnidrive-a-holistic-vision-language-dataset-for-autonomous-driving-with-counterfactual-reasoning"
authors:
  - "Shihao Wang"
  - "Zhiding Yu"
  - "Xiaohui Jiang"
  - "Shiyi Lan"
  - "Min Shi"
  - "Nadine Chang"
  - "Jan Kautz"
  - "Ying Li"
  - "Jose M. Alvarez"
paper_category: "VLA"
compiled_at: "2026-05-07T13:22:07.411329+00:00"
compile_model: "gpt-5.4"
source_signature: "8e2f4c4e6ea11f81420f9b6cc7370026a4249203"
source_record: "data/paper_records/0015-2405.01533v2.md"
---

# OmniDrive: A Holistic Vision-Language Dataset for Autonomous Driving with Counterfactual Reasoning

## 一句话定位

[[OmniDrive]] 是一个面向[[自动驾驶]]的整体式视觉-语言数据集与训练范式，核心在于把“专家轨迹监督”扩展为带有[[反事实推理]]的 3D 驾驶问答数据，用于提升驾驶智能体的解释、理解与规划能力。

## 核心贡献

- 提出以[[反事实推理]]为核心的 3D 驾驶数据构造流程。
- 基于[[nuScenes]]、道路拓扑信息、候选轨迹模拟与规则检查，构建 [[OmniDrive]] 数据集。
- 设计并比较两条路线：
  - [[Omni-L]]：从强 2D [[视觉语言模型]] 往 3D 驾驶扩展，通过 [[MLP投影]] 将视觉特征接入语言模型。
  - [[Omni-Q]]：从 3D 感知系统接入语言模型，使用 [[BEV表征]] 与 [[Q-Former]] 压缩多视角视觉信息。
- 实验证明：[[OmniDrive]] 预训练能同时提升[[DriveLM]]问答与[[nuScenes]]开环规划表现。
- 论文的核心论点是：相比只学习专家轨迹，加入“如果这样开会发生什么”的反事实监督，能让模型更好地形成因果层面的驾驶理解。

## 方法

### 整体思路

论文的方法可以概括为两阶段：

1. **先造数据**：从[[nuScenes]]场景中构造带反事实结论的 3D 驾驶问答数据。
2. **再训模型**：用这些数据训练驾驶视觉语言智能体，并比较不同多模态对齐路线的效果。

其直观动机是：传统方法更像“背标准路线”，而 [[OmniDrive]] 试图让模型学习不同候选行为对应的后果，如是否会碰撞、闯红灯或驶出可行驶区域。

### 数据构造流程

根据材料，[[OmniDrive]] 的数据生成流程包括：

- 从[[nuScenes]]中选择关键帧；
- 先利用 [[CLIP]] 图像特征做聚类，筛选语义上有代表性的场景；
- 再根据未来轨迹做聚类，以覆盖直行、左转、右转、掉头、加减速等行为模式；
- 在每个场景内构造多种[[轨迹仿真]]候选；
- 用[[规则检查]]判断这些候选轨迹的后果，包括：
  - 是否碰撞
  - 是否闯红灯
  - 是否驶出可行驶区域
- 将高层后果、3D 物体信息、车道与地图元素、专家轨迹以及拼接后的多视角图像一起交给 [[GPT-4]]，生成：
  - 场景描述
  - 注意对象
  - 反事实问答

这里，[[OpenLane-V2]] 被用于道路拓扑与规则检查辅助。

### 两条模型路线

#### [[Omni-L]]

[[Omni-L]] 代表“从强 2D [[视觉语言模型]] 往 3D 驾驶扩展”的路线。其做法是：

- 使用视觉编码器提取图像特征；
- 通过 [[MLP投影]] 将视觉表示映射到语言模型可消费的空间；
- 结合 3D 场景与任务提示，生成问答和规划结果。

对应公式为：

\[
z = W_p h_v
\]

含义是将视觉特征 \(h_v\) 投影到语言空间中的表示 \(z\)。

#### [[Omni-Q]]

[[Omni-Q]] 代表“从 3D 感知系统接到语言模型”的路线。其核心组件包括：

- [[BEV表征]] 作为 3D 感知主干；
- 使用 [[Q-Former]] 对多视角高分辨率视觉信息进行压缩；
- 将压缩后的视觉 token 提供给语言模型完成问答与规划。

其关键机制是 [[交叉注意力]]：

\[
Q' = \mathrm{CrossAttn}(Q, V) = \mathrm{softmax}\left(\frac{QW_Q (VW_K)^\top}{\sqrt{d}}\right) VW_V
\]

直观上，少量 query token 从大量视觉 token 中提取最相关信息，避免直接把全部高分辨率图像塞进语言模型。

### 训练目标与评测指标

语言建模目标为：

\[
\mathcal{L}_{\mathrm{LM}} = - \sum_t \log p(y_t \mid y_{<t}, x)
\]

即在给定图像、3D 场景和提示后，学习生成正确的文本输出。

反事实评测使用了精确率与召回率：

\[
\mathrm{Precision} = \frac{TP}{TP+FP}, \quad \mathrm{Recall} = \frac{TP}{TP+FN}
\]

材料中说明，该评测通过比较模型输出中的关键词与真值，衡量其是否正确判断碰撞、闯红灯、越界等后果。

## 实验与结论

### 数据集与基准

论文涉及的数据集与用途包括：

- [[nuScenes]]：数据生成、训练与评测
- [[OpenLane-V2]]：道路拓扑与规则检查辅助
- [[DriveLM]]：问答基准评测
- [[LLaVA665k]]：额外预训练

对比对象包括 [[Omni-Q]]、[[BEV-MLP]]、[[DriveLM]]、[[LLaVA v1.5]]、[[BEV-Planner]]。

### 主要结果

#### [[OmniDrive]] 预训练有效

在[[DriveLM]]问答基准上：

- 只用 [[DriveLM]] 训练时，总分为 **0.53**
- 加入 [[OmniDrive]] 预训练后，总分提升到 **0.56**
- 再结合 [[LLaVA665k]]，可达到 **0.58**

这说明 [[OmniDrive]] 提供的驾驶语言-空间对齐监督具有稳定增益。

#### [[Omni-L]] 在反事实与规划上优于 [[BEV-MLP]]

在[[nuScenes]]开环规划与反事实能力上，[[Omni-L]] 相比 [[BEV-MLP]] 有明显提升：

- 反事实 AP/AR/CIDEr：
  - [[Omni-L]]：**53.7 / 63.0 / 73.2**
  - [[BEV-MLP]]：**45.6 / 49.5 / 59.5**
- 规划碰撞率：
  - [[BEV-MLP]]：**4.43%**
  - [[Omni-L]]：**1.90%**
- 越界率：
  - [[BEV-MLP]]：**8.56%**
  - [[Omni-L]]：**3.29%**

这些结果支持论文的核心结论：加入反事实数据监督不仅提升语言问答，也能改善规划质量。

#### [[Omni-L]] 优于 [[Omni-Q]]

论文还发现，[[Omni-L]] 整体表现优于 [[Omni-Q]]。这表明：

- 从成熟的 2D [[视觉语言模型]] 出发，向 3D 驾驶场景扩展，是更直接有效的路线；
- 相比之下，将传统 3D 感知栈深度接入语言空间，当前仍更复杂且效果较弱。

### 结论解读

这篇工作的实验结论可以概括为两点：

1. [[OmniDrive]] 这种带[[反事实推理]]的数据，确实比单纯专家轨迹监督更有信息密度；
2. 在当前阶段，“强语言底座 + 3D 对齐”比“3D 感知 + 语言接入”更有优势。

## 限制与待解

论文材料中提到的局限主要包括：

- 反事实结果主要来自规则与开环模拟，并未真正建模其他交通参与者的交互反应，因此更接近“单车视角的后果想象”，而非完整多车博弈。
- 数据生成高度依赖 [[GPT-4]]、人工质检与规则设计，成本较高，且数据质量受提示词与规则清单质量影响明显。
- 部分反事实能力评测依赖关键词抽取，与真实驾驶安全之间仍存在距离。
- 传统 3D 感知栈与语言空间的深度融合仍不成熟，复现时模块耦合较复杂。

## 涉及概念

- [[OmniDrive]]
- [[自动驾驶]]
- [[视觉语言模型]]
- [[反事实推理]]
- [[反事实问答]]
- [[3D场景理解]]
- [[开放环规划]]
- [[多视角图像]]
- [[nuScenes]]
- [[DriveLM]]
- [[OpenLane-V2]]
- [[LLaVA665k]]
- [[Omni-L]]
- [[Omni-Q]]
- [[Q-Former]]
- [[交叉注意力]]
- [[MLP投影]]
- [[BEV表征]]
- [[轨迹仿真]]
- [[规则检查]]
- [[K-means聚类]]
- [[CLIP]]
- [[GPT-4]]
