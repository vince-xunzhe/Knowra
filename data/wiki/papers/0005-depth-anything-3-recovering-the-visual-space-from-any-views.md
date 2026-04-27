---
kind: "paper"
title: "Depth Anything 3: Recovering the Visual Space from Any Views"
paper_id: 5
slug: "depth-anything-3-recovering-the-visual-space-from-any-views"
authors:
  - "Haotong Lin"
  - "Sili Chen"
  - "Jun Hao Liew"
  - "Donny Y. Chen"
  - "Zhenyu Li"
  - "Guang Shi"
  - "Jiashi Feng"
  - "Bingyi Kang"
compiled_at: "2026-04-26T16:16:48.612143+00:00"
compile_model: "gpt-4o-mini"
source_record: "data/paper_records/0005-2511.10647v1.md"
---

# Depth Anything 3: Recovering the Visual Space from Any Views

## 一句话定位
论文提出了一种名为[[Depth Anything 3 (DA3)]]的模型，用于从任意数量的视觉输入中恢复空间一致的几何图形。

## 核心贡献
论文的核心贡献在于提出了一种统一单视图与多视图几何预测的简单建模方法，使用单一[[变压器]]，并结合深度与射线目标。

## 方法
DA3模型通过单一[[变压器]]架构，将二维视觉信息转化为深度和射线图，最终通过一个双[[DPT头]]预测结果。模型从任意数量的图片中获取输入，这些图片可能带有或不带相机位置。主要步骤包括：
- **深度地图预测**：从输入图像中生成深度地图与射线图，这些预测是相机视角无关的。
- **射线表示**：将输入特征转换为用于深度和射线的不同输出，通过融合参数结合在一起。

## 实验与结论
在新基准的相机位置精度上，DA3实现了35.7%的提升，在几何精度上提升了23.6%。与DA2相比，单目深度估计也有显著提高。DA3在所有任务上设立了新的最先进记录，展现了其在视觉几何领域的潜力。

## 限制与待解
尽管模型实现了通用性，但在动态场景和需交互语言提示的应用中可能较弱。此外，虽然模型架构简化了，但训练过程仍然依赖高质量的数据集，计算成本较高。

## 涉及概念
- [[变压器]]
- [[自注意力]]
- [[双DPT头]]
- [[ETH3D]]
- [[7Scenes]]
- [[ScanNet++]]
