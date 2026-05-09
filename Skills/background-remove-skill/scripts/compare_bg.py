"""
对比图生成脚本：将原图与去背景结果并排展示在灰色底上
用法：python compare_bg.py <原图路径> <去背景结果路径> [输出对比图路径]
"""

import sys
import os
from PIL import Image

orig_path   = sys.argv[1]
result_path = sys.argv[2]
output_path = sys.argv[3] if len(sys.argv) > 3 else None

def on_gray(img: Image.Image) -> Image.Image:
    """将带透明通道的图片合成到灰色背景上"""
    img = img.convert("RGBA")
    bg = Image.new("RGBA", img.size, (180, 180, 180, 255))
    bg.paste(img, mask=img.split()[3])
    return bg.convert("RGB")

orig   = on_gray(Image.open(orig_path))
result = on_gray(Image.open(result_path))

# 对比图宽度为两张图并排，高度取较大值
w = orig.width + result.width
h = max(orig.height, result.height)
compare = Image.new("RGB", (w, h), (180, 180, 180))
compare.paste(orig,   (0, 0))
compare.paste(result, (orig.width, 0))

if output_path is None:
    result_dir  = os.path.dirname(os.path.abspath(result_path))
    result_stem = os.path.splitext(os.path.basename(result_path))[0]
    output_path = os.path.join(result_dir, f"{result_stem}_compare.jpg")

compare.save(output_path, quality=95)
print(f"对比图已保存：{output_path}")
