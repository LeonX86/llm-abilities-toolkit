---
name: background-remove-skill
description: 纯色背景去除（抠图）技能。当用户需要去除图片白色或黑色背景、抠图、保留透明底、处理贴纸/表情包/图标，或需要导出透明底GIF用于QQ表情时使用。支持细节保留（如内部同色区域、半透明边缘等），并提供边缘毛边优化参数。用户提到"抠图"、"去背景"、"白底变透明"、"黑底变透明"、"去掉白色背景"、"去掉黑色背景"、"边缘毛边"、"QQ表情"、"发QQ有黑底"时必须触发此 skill。
---

# 纯色背景去除 Skill

适用于：白色或黑色纯色背景图片、贴纸、表情包、卡通插画、图标，输出带透明通道的 PNG 文件；支持进一步导出透明底 GIF，用于 QQ 等只支持 GIF 透明底的平台。

---

## 工作流程

### 第一步：确认输入信息

在开始前确认以下信息（如对话中已提供则直接使用，无需重复询问）：

1. **输入图片**：文件路径或上传的图片
2. **背景颜色**：对应 `--mode white` 或 `--mode black`
   - **如果你（模型）具备视觉能力且用户已提供图片**：直接观察图片背景颜色，自行判断使用哪个 mode，无需询问用户
   - **如果你没有视觉能力，或无法判断**：询问用户"请问图片是白色背景还是黑色背景？"
3. **输出文件夹**（可选）：用户可指定输出目录，默认输出到与输入图片相同的目录
4. **初始参数**：首次运行使用默认参数，告知用户后续可调整

---

### 第二步：首次运行（使用默认参数）

先验证依赖是否已安装：
```bash
python -c "import PIL, numpy, scipy; print('依赖已满足')"
```

若提示 `ModuleNotFoundError`，再执行安装：
```bash
pip install Pillow numpy scipy -q
```

运行脚本：
```bash
python /path/to/scripts/remove_bg.py \
  <输入图片路径> \
  [输出文件夹，可选] \
  [腐蚀像素数，默认2] \
  [边缘透明度0~1，默认0.1] \
  [--mode white|black，默认white]
```

**示例：**
```bash
# 去除白色背景（默认，结果输出到图片同目录）
python scripts/remove_bg.py C:/Users/me/images/sticker.jpg

# 去除黑色背景
python scripts/remove_bg.py C:/Users/me/images/sticker.png --mode black

# 指定输出文件夹 + 黑色背景
python scripts/remove_bg.py C:/Users/me/images/sticker.png C:/Users/me/output --mode black

# 完整参数（黑色背景，腐蚀3px，边缘透明度0.05）
python scripts/remove_bg.py C:/Users/me/images/sticker.png C:/Users/me/output 3 0.05 --mode black
```

运行完成后，将结果图片路径告知用户，并询问效果是否满意。

---

### 第三步：生成对比图（推荐）

在灰色背景上将原图与结果并排展示，方便用户肉眼判断边缘效果。直接运行现成脚本：

```bash
python /path/to/scripts/compare_bg.py <原图路径> <去背景结果路径> [输出对比图路径（可选）]
```

**示例：**
```bash
# 输出路径可选，默认保存到结果图片同目录，文件名加 _compare 后缀
python scripts/compare_bg.py C:/Users/me/images/sticker.png C:/Users/me/images/sticker_nobg.png

# 指定输出路径
python scripts/compare_bg.py C:/Users/me/images/sticker.png C:/Users/me/images/sticker_nobg.png C:/Users/me/images/compare.jpg
```

---

### 第四步：引导用户根据结果调参

运行后**必须**向用户展示结果并说明可调参数，引导他们反馈：

> 效果怎么样？如果还有毛边或边缘不理想，可以调整以下参数重新处理：

| 参数 | 说明 | 默认值 | 调整方向 |
|------|------|--------|----------|
| **`--mode`** | 背景颜色模式 | `white` | 黑色背景图片 → `black` |
| **腐蚀像素数**（`erosion_px`） | 向内收边多少像素，越大去掉的边缘越多 | `2` | 毛边多 → 调大（3~4）；边缘被吃掉 → 调小（1） |
| **边缘透明度**（`edge_opacity`） | 腐蚀掉的边缘区域保留多少不透明度（0~1） | `0.1` | 毛边明显 → 调小（0.05）；边缘太硬 → 调大（0.2~0.3） |
| **白色阈值**（`WHITE_THRESH`，脚本内修改） | 白色模式下判定为背景的亮度下限 | `240` | 背景去不干净 → 调小（220）；误删内容 → 调大（250） |
| **黑色阈值**（`BLACK_THRESH`，脚本内修改） | 黑色模式下判定为背景的亮度上限 | `15` | 背景去不干净 → 调大（25）；误删内容 → 调小（8） |

**常见问题对应调参建议：**
- **还有毛边残留** → `erosion_px` 调大到 3~4，`edge_opacity` 调小到 0.05
- **边缘被吃得太多** → `erosion_px` 调小到 1，`edge_opacity` 调大到 0.2
- **背景没去干净** → 对应调整 `WHITE_THRESH`（白色模式）或 `BLACK_THRESH`（黑色模式）
- **主体内部同色区域被误删** → 该区域与背景不连通则不会被删；若被删说明存在连通路径，需调整阈值

---

### 第五步：迭代调整

根据用户反馈，修改参数重新运行脚本，直到用户满意为止。每次运行后都生成新的对比图。

---

### 第六步：导出 GIF（可选，用于 QQ 等平台）

**在用户对 PNG 效果满意后**，若用户需要发送到 QQ，再执行此步骤。
QQ 自定义表情只有 GIF 格式才能正确渲染透明底，PNG 会被盖上黑色背景。
**当用户提到"发到QQ"、"QQ表情"、"发QQ有黑底"时，主动提示并执行此步骤。**

> 注意：GIF 只支持 1-bit 透明度，边缘半透明像素会被二值化（阈值128），对卡通贴纸影响极小。

```bash
python /path/to/scripts/to_gif.py <去背景PNG路径> [输出GIF路径（可选）]
```

**示例：**
```bash
# 默认输出到 PNG 同目录，文件名相同但后缀改为 .gif
python scripts/to_gif.py C:/Users/me/images/sticker_nobg.png

# 指定输出路径
python scripts/to_gif.py C:/Users/me/images/sticker_nobg.png C:/Users/me/images/sticker.gif
```

---

## 技术原理

### 两阶段处理

**阶段一：洪水填充（Flood Fill BFS）去除外部背景**
- 从图片四条边上的背景色像素出发，向内 BFS 扩散
- 只删除"从边缘可连通"的背景色区域
- 优点：主体内部的同色区域（如黑色描边、白色细节）完全不受影响

**阶段二：腐蚀 + 高斯羽化去除毛边**
- `binary_erosion`：向内收缩不透明区域，去掉边缘残留的半透明杂色像素
- `gaussian_filter`：对边缘区域做平滑过渡，避免收边后出现硬边
- 两者配合产生自然的边缘羽化效果

### 适用场景
- 白色或接近白色的纯色背景
- 黑色或接近黑色的纯色背景
- 主体颜色与背景有明显差异
- 贴纸、表情包、卡通插画、图标
- 主体内部含有与背景相同颜色时，依赖 BFS 连通性判断，通常效果仍然良好
- 复杂自然场景背景（建议改用 `rembg` AI 抠图库）

---

## 依赖

```
Pillow
numpy
scipy
```
