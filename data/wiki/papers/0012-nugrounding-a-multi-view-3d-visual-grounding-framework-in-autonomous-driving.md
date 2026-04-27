---
kind: "paper"
title: "NuGrounding: A Multi-View 3D Visual Grounding Framework in Autonomous Driving"
paper_id: 12
slug: "nugrounding-a-multi-view-3d-visual-grounding-framework-in-autonomous-driving"
authors:
  - "Fuhao Li"
  - "Huan Jin"
  - "Bin Gao"
  - "Liaoyuan Fan"
  - "Lihui Jiang"
  - "Long Zeng"
compiled_at: "2026-04-26T16:18:12.556658+00:00"
compile_model: "gpt-4o-mini"
source_record: "data/paper_records/0012-2503.22436v2.md"
---

# NuGrounding: A Multi-View 3D Visual Grounding Framework in Autonomous Driving

## 一句话定位
提出了一种多视角3D视觉定位框架[[NuGrounding]]，旨在通过自然语言指令在自动驾驶场景中定位3D目标。

## 核心贡献
本文的核心贡献在于提出了[[NuGrounding]]数据集，并结合多模态大语言模型（[[MLLM]]）的理解能力与3D检测器的定位能力，构建了一套多视角3D语言定位框架。

## 方法
论文的方法主要包括以下几个步骤：
1. 输入为自动驾驶车辆的多视角图像和一条文本指令。
2. [[BEV检测]]器将多摄像头图像投影到鸟瞰视角，提取[[BEV特征]]，并生成一组[[对象查询]]，每个查询代表一个候选物体的3D信息。
3. [[对象查询]]经过适配器转化为[[MLLM]]可读的视觉token，并与文本token拼接。
4. [[MLLM]]在生成回答时使用任务标记[[DET]]和[[EMB]]，并用可学习的[[上下文查询]]替换[[EMB]]位置，以吸收文本语义和3D场景信息。
5. 最后，[[融合解码器]]通过自注意力和交叉注意力机制融合空间与语义信息，输出目标3D边界框。

## 实验与结论
实验结果表明，[[NuGrounding]]方法在多个指标上显著优于现有方法。使用[[ViT-B]]模型时，Precision达到0.59，Recall为0.64，mAP为0.40，NDS为0.48，相比于[[NuPrompt]]的[[ViT-L]]版本分别提升了0.30、0.35、0.29和0.26。此外，消融实验显示，加入[[上下文查询]]后mAP/NDS从0.387/0.445提升至0.443/0.497，证明了设计的有效性。

## 限制与待解
该研究存在一些实际限制：
1. 数据来源于[[NuScenes]]，在不同国家、天气和传感器布局下可能需要重新适配。
2. 外观和运动属性依赖于规则和人工校验，可能导致标注混乱。
3. 模型的计算和部署成本较高，不一定适合实时系统。
4. 主要覆盖封闭类别的3D框定位，对开放词表和长时序交互的验证不足。

## 涉及概念
- [[NuGrounding]]
- [[NuScenes]]
- [[BEV检测]]
- [[对象查询]]
- [[MLLM]]
- [[任务令牌]]
- [[上下文查询]]
- [[查询选择]]
- [[融合解码]]
- [[HoG]]
