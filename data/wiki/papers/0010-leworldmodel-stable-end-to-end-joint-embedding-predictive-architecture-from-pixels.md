---
kind: "paper"
title: "LeWorldModel: Stable End-to-End Joint-Embedding Predictive Architecture from Pixels"
paper_id: 10
slug: "leworldmodel-stable-end-to-end-joint-embedding-predictive-architecture-from-pixels"
authors:
  - "Lucas Maes"
  - "Quentin Le Lidec"
  - "Damien Scieur"
  - "Yann LeCun"
  - "Randall Balestriero"
paper_category: "世界模型"
compiled_at: "2026-05-07T13:19:44.009628+00:00"
compile_model: "gpt-5.4"
source_signature: "2a36f34641264e839ee819300d131887cd756bb9"
source_record: "data/paper_records/0010-2603.19312v2.md"
---

# LeWorldModel: Stable End-to-End Joint-Embedding Predictive Architecture from Pixels

## 一句话定位

[[LeWorldModel]] 是一个面向[[强化学习]]中[[世界模型]]与表征学习的端到端像素方法：它在[[JEPA]]框架下，只用“下一潜表示预测损失 + [[SIGReg]] 反坍塌正则”两项损失，就能稳定学出可用于规划的潜空间动力学模型。

## 核心贡献

- 提出 [[LeWorldModel]]，直接从原始像素和动作联合学习可规划的[[潜空间动力学]]，不依赖预训练特征、重建目标、奖励信号、[[EMA]] 或 stop-gradient。
- 将端到端像素[[JEPA]]世界模型简化为极简两项目标：
  - 下一嵌入预测误差；
  - [[SIGReg]] 分布正则。
- 用 [[SIGReg]] 作为反坍塌机制，从分布层面约束潜表示接近各向同性高斯，避免表示塌缩为常数。
- 在多种 2D/3D 控制任务上，相比已有端到端方法展现出更好的稳定性、较少的超参数负担和更高的规划效率。
- 模型规模较小，摘要称约 1500 万参数，单张 GPU 数小时即可训练。

## 方法

### 整体思路

[[LeWorldModel]] 延续了[[JEPA]]“在潜空间预测未来而不是重建像素”的路线。

给定离线轨迹中的连续图像观察和动作序列：

1. 先用编码器把每一帧像素映射到低维潜向量；
2. 再用预测器根据历史潜向量与动作，自回归预测下一时刻潜表示；
3. 训练时同时优化预测准确性与潜空间分布健康性；
4. 测试时在潜空间里做[[模型预测控制]]，搜索动作序列使未来终点接近目标图像的潜表示。

### 架构

- **编码器**：将图像送入 [[ViT]]，取最后一层的 `[CLS]` 表示，再经过带 [[BatchNorm]] 的投影头得到潜表示 \(z\)。
- **预测器**：读取历史潜表示与动作序列，使用带因果掩码的 [[Transformer]] 自回归预测下一潜表示 \(\hat z\)。

可概括为：

- \(z_t = \mathrm{enc}_\theta(o_t)\)
- \(\hat z_{t+1} = \mathrm{pred}_\phi(z_t, a_t)\)

### 训练目标

[[LeWorldModel]] 的总损失只有两项。

#### 下一嵌入预测

\[
L_{pred} \triangleq \|\hat{z}_{t+1} - z_{t+1}\|_2^2, \quad \hat{z}_{t+1} = \mathrm{pred}_{\phi}(z_t, a_t)
\]

作用：让潜空间真正承载可预测的环境动力学。

#### [[SIGReg]] 正则

\[
\mathrm{SIGReg}(Z) \triangleq \frac{1}{M} \sum_{m=1}^{M} T(h^{(m)})
\]

作用：沿多个随机方向考察潜表示投影的一维分布是否接近高斯，从而约束整体潜空间接近各向同性高斯，防止表示坍塌。

#### 总损失

\[
L_{\mathrm{LeWM}} \triangleq L_{pred} + \lambda \,\mathrm{SIGReg}(Z)
\]

这也是论文强调的核心简化：相比依赖多项复杂损失的方案，主要只需调节 \(\lambda\)。

### 为什么这样能稳定

论文给出的关键判断是：

- 单独做未来预测，模型可能把所有输入压成几乎相同的表示，虽然“好预测”，但失去信息；
- 单独做分布约束又不能保证学到环境动力学；
- 将“未来可预测”与“分布不坍塌”结合起来，形成了一个更清晰的训练信号。

相比需要 [[VICReg]] 式复杂多损失设计的方法，这种做法训练更平滑、超参数更少、实现门槛更低。

### 规划

测试时固定模型：

1. 将起点观测和目标观测分别编码为潜表示；
2. 用预测器 rollout 候选动作序列对应的未来潜轨迹；
3. 用[[CEM]]优化动作序列，使预测终点接近目标潜表示。

规划目标为：

\[
C(\hat{z}_H) = \|\hat{z}_H - z_g\|_2^2, \quad z_g = \mathrm{enc}_{\theta}(o_g), \quad a^*_{1:H} = \arg\min_{a_{1:H}} C(\hat{z}_H)
\]

这属于典型的[[模型预测控制]]式潜空间规划。

## 实验与结论

### 评测设置

实验覆盖的数据集/任务包括：

- [[Two-Room]]
- [[Reacher]]
- [[Push-T]]
- [[OGBench-Cube]]

设定强调离线、无奖励学习，再通过规划完成目标驱动控制。

### 主要结果

- 在 [[Push-T]] 上，[[LeWorldModel]] 成功率为 **96%**：
  - 高于 [[PLDM]] 的 **78%**
  - 高于 [[DINO-WM]] 的 **74%**
  - 高于带额外 proprioception 的 [[DINO-WM]] 的 **92%**
- 在 [[Reacher]] 上为 **86%**：
  - 高于 [[PLDM]] 的 **78%**
  - 高于 [[DINO-WM]] 的 **79%**
- 在 [[OGBench-Cube]] 上为 **74%**：
  - 高于 [[PLDM]] 的 **65%**
  - 低于 [[DINO-WM]] 的 **86%**

### 规划效率

论文特别强调规划速度优势：

- [[LeWorldModel]] 完整规划时间约 **0.98 秒**
- [[DINO-WM]] 约 **47 秒**

约快 **48 倍**。

在固定 FLOPs 下：

- [[Push-T]]：**90% vs 13%**
- [[OGBench-Cube]]：**74% vs 48%**

说明其优势不只是“更省算力”，而是在单位算力条件下也更有效。

### 结论

论文结论可以概括为：

- 端到端像素[[世界模型]]并不一定需要复杂训练技巧；
- “预测损失 + [[SIGReg]]”足以稳定学出可规划潜空间；
- 该路线在训练门槛、规划速度和综合性能之间取得了很强的平衡。

## 限制与待解

- [[SIGReg]] 假设潜表示整体接近各向同性高斯，这种约束在低维、低多样性环境（如 [[Two-Room]]）中可能偏强，未必最贴合任务结构。
- 方法采用自回归潜空间 rollout，随着规划 horizon 增长会累积误差，因此仍依赖[[模型预测控制]]中的反复重规划。
- 在更复杂的 3D 场景上，结果仍可能落后于 [[DINO-WM]]，说明纯端到端像素编码在高视觉复杂度下还有改进空间。
- 论文主要验证的是离线、无奖励设定；对开放世界、长时程、多任务、真实机器人场景的泛化仍需进一步验证。

## 涉及概念

- [[LeWorldModel]]
- [[JEPA]]
- [[Joint Embedding Predictive Architecture]]
- [[世界模型]]
- [[潜空间动力学]]
- [[SIGReg]]
- [[反坍塌正则化]]
- [[模型预测控制]]
- [[CEM]]
- [[ViT]]
- [[Transformer]]
- [[BatchNorm]]
- [[离线无奖励学习]]
- [[PLDM]]
- [[DINO-WM]]
- [[VICReg]]
- [[EMA]]
- [[Two-Room]]
- [[Reacher]]
- [[Push-T]]
- [[OGBench-Cube]]

## 历史定位

[[LeWorldModel]] 直接建立在 [[Yann LeCun]] 提出的[[JEPA]]思路之上，延续了“在潜空间建模未来”的路线，也继承了[[世界模型]]与[[模型预测控制]]结合的规划范式。

它的主要方法学位置在于：

- 用 [[SIGReg]] 替代更复杂的[[VICReg]]式多损失方案；
- 将“端到端、像素级、无预训练、少超参数、稳定训练”同时做到；
- 将一类此前较复杂、较难调的原型方法，推进到更实用、更易复现的形态。

从论文定位看，它也可能推动后续研究继续探索：

- 用统计分布约束稳定表征学习；
- 用轻量潜空间模型支持快速规划；
- 用更偏“理解力”的 probing 或预期违背检测来评估[[世界模型]]。
