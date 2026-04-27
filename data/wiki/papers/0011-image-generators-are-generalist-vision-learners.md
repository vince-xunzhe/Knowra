---
kind: "paper"
title: "Image Generators are Generalist Vision Learners"
paper_id: 11
slug: "image-generators-are-generalist-vision-learners"
authors:
  - "Valentin Gabeur"
  - "Shangbang Long"
  - "Songyou Peng"
  - "Paul Voigtlaender"
  - "Shuyang Sun"
  - "Yanan Bao"
  - "Karen Truong"
  - "Zhicheng Wang"
  - "Wenlei Zhou"
  - "Jonathan T. Barron"
  - "Kyle Genova"
  - "Nithish Kannen"
  - "Sherry Ben"
  - "Yandong Li"
  - "Mandy Guo"
  - "Suhas Yogin"
  - "Yiming Gu"
  - "Huizhong Chen"
  - "Oliver Wang"
  - "Saining Xie"
  - "Howard Zhou"
  - "Kaiming He"
  - "Thomas Funkhouser"
  - "Jean-Baptiste Alayrac"
  - "Radu Soricut"
compiled_at: "2026-04-26T16:17:58.296976+00:00"
compile_model: "gpt-4o-mini"
source_record: "data/paper_records/0011-2604.20329v1.md"
---

# Image Generators are Generalist Vision Learners

## 一句话定位
这篇论文提出了一种新的视觉理解模型 [[Vision Banana]]，通过对强大的图像生成器 [[Nano Banana Pro]] 进行轻量指令微调，实现了同时处理分割、深度和法线等多种视觉任务的能力。

## 核心贡献
论文的核心贡献在于将图像生成器轻量指令微调成能执行多种视觉理解任务的通用模型，避免了为每个任务单独设计网络的复杂性。

## 方法
### 原理
- **类比**：将 [[Nano Banana Pro]] 比作一个会画画的学生，通过补充“答题格式课”来学习如何将视觉任务转化为可解码的 RGB 图像。
- **架构流程**：输入为图像和自然语言提示，模型根据提示生成符合约定格式的 RGB 可视化结果，输出后通过评测程序解码得到任务结果。
  
### 关键公式
1. **式(1) 深度幂变换**：将真实深度压缩到 0 到 1 的范围，以便更好地表达深度差异。
2. **深度到 RGB 的可逆映射**：通过固定路径将压缩后的深度值映射为颜色，确保可逆性。
3. **分割颜色匹配解码**：通过比较像素与提示颜色的距离来确定类别标签。
4. **表面法线 RGB 编码**：将三维朝向向量映射到 RGB 通道，以便还原局部几何方向。

## 实验与结论
实验结果表明，统一模型在多个任务上超越了专用模型的表现，例如在 [[Cityscapes]] 语义分割上 mIoU 达到 0.699，超过了 [[SAM 3]] 的 0.652。同时，模型在深度和法线估计上也表现出色，基本保留了生成能力。

## 限制与待解
尽管论文展示了强结果，但模型依赖闭源的 [[Nano Banana Pro]]，训练细节和数据规模难以复现。此外，推理成本高于轻量专用模型，实例分割仍落后于 [[SAM 3]]，且在视频、多视角和实时部署等方面尚未充分验证。

## 涉及概念
- [[Vision Banana]]
- [[Nano Banana Pro]]
- [[图像生成]]
- [[指令微调]]
- [[RGB接口]]
- [[生成式分割]]
- [[深度编码]]
- [[法线编码]]
- [[零样本迁移]]
