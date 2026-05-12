"""
PNG 转透明 GIF 脚本（用于 QQ 等只支持 GIF 透明底的平台）
用法：python to_gif.py <输入PNG路径> [输出GIF路径]

注意：GIF 只支持 1-bit 透明度，边缘半透明像素会被二值化（阈值128），
      对卡通贴纸影响极小，但照片级图片边缘会略显生硬。
"""

import sys
import os
import numpy as np
from PIL import Image

if len(sys.argv) < 2:
    print("用法：python to_gif.py <输入PNG路径> [输出GIF路径]")
    sys.exit(1)

input_path  = sys.argv[1]
output_path = sys.argv[2] if len(sys.argv) > 2 else None

if not os.path.isfile(input_path):
    print(f"错误：输入文件不存在：{input_path}")
    sys.exit(1)

if output_path is None:
    stem        = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(os.path.dirname(os.path.abspath(input_path)), f"{stem}.gif")

img = Image.open(input_path).convert("RGBA")
r, g, b, a = img.split()

# 二值化 alpha：>= 128 完全不透明，< 128 完全透明（GIF 限制）
a_binary = np.array(a)
transparent_mask = a_binary < 128  # True = 需要透明的像素

# 转换为调色板模式（最多255色，保留1个槽位给透明色）
rgb = Image.merge("RGB", (r, g, b))
quantized = rgb.quantize(colors=255)

# 在调色板末尾（索引255）放入透明色占位
palette = quantized.getpalette()
palette[255 * 3: 255 * 3 + 3] = [0, 0, 0]
quantized.putpalette(palette)

# 将需要透明的像素索引设为255
pixels = np.array(quantized)
pixels[transparent_mask] = 255
result = Image.fromarray(pixels, mode="P")
result.putpalette(palette)

result.save(output_path, transparency=255)
print(f"GIF 已保存：{output_path}")
print("提示：GIF 边缘为硬边（1-bit透明），如需柔和边缘请使用 PNG 版本")
