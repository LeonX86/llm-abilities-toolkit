"""
背景去除脚本，支持白色和黑色背景
用法：python remove_bg.py <输入图片路径> [输出文件夹] [腐蚀像素数] [边缘透明度] [--mode white|black]
"""

import sys
import os
import argparse
import numpy as np
from PIL import Image
from scipy.ndimage import binary_erosion, gaussian_filter
from collections import deque

# ── 参数解析 ──────────────────────────────────────────────
parser = argparse.ArgumentParser(description="背景去除脚本，支持白色和黑色背景")
parser.add_argument("input_path", help="输入图片路径")
parser.add_argument("output_dir", nargs="?", default=None, help="输出文件夹（可选）")
parser.add_argument("erosion_px", nargs="?", type=int, default=2, help="腐蚀像素数，默认2")
parser.add_argument("edge_opacity", nargs="?", type=float, default=0.1, help="边缘透明度0~1，默认0.1")
parser.add_argument("--mode", choices=["white", "black"], default="white", help="背景颜色模式：white（默认）或 black")

args = parser.parse_args()

input_path   = args.input_path
output_dir   = args.output_dir
erosion_px   = args.erosion_px
edge_opacity = args.edge_opacity
mode         = args.mode

# ── 输出路径处理 ───────────────────────────────────────────
basename = os.path.splitext(os.path.basename(input_path))[0]
if output_dir:
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{basename}_nobg.png")
else:
    # 默认输出到与输入图片相同的目录
    output_path = os.path.join(os.path.dirname(os.path.abspath(input_path)), f"{basename}_nobg.png")

print(f"输入：{input_path}")
print(f"输出：{output_path}")
print(f"模式：{'白色背景' if mode == 'white' else '黑色背景'}  |  腐蚀像素：{erosion_px}px  |  边缘透明度：{int(edge_opacity*100)}%")

# ── 第一步：洪水填充去除背景 ────────────────────────────────
# 从图片四条边向内 BFS，只去掉"外部连通"的背景色区域
# 主体内部的同色区域不会被误删（依赖连通性判断）

img = Image.open(input_path).convert("RGBA")
data = np.array(img)
h, w = data.shape[:2]

WHITE_THRESH = 240  # 白色判定：三通道均 >= 此值
BLACK_THRESH = 15   # 黑色判定：三通道均 <= 此值

def is_background(px: np.ndarray) -> bool:
    if mode == "white":
        return int(px[0]) >= WHITE_THRESH and int(px[1]) >= WHITE_THRESH and int(px[2]) >= WHITE_THRESH
    else:
        return int(px[0]) <= BLACK_THRESH and int(px[1]) <= BLACK_THRESH and int(px[2]) <= BLACK_THRESH

visited = np.zeros((h, w), dtype=bool)
bg_mask = np.zeros((h, w), dtype=bool)

queue = deque()

for x in range(w):
    for y in [0, h - 1]:
        if not visited[y, x] and is_background(data[y, x]):
            visited[y, x] = True
            bg_mask[y, x] = True
            queue.append((y, x))
for y in range(h):
    for x in [0, w - 1]:
        if not visited[y, x] and is_background(data[y, x]):
            visited[y, x] = True
            bg_mask[y, x] = True
            queue.append((y, x))

while queue:
    cy, cx = queue.popleft()
    for dy, dx in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
        ny, nx = cy + dy, cx + dx
        if 0 <= ny < h and 0 <= nx < w and not visited[ny, nx] and is_background(data[ny, nx]):
            visited[ny, nx] = True
            bg_mask[ny, nx] = True
            queue.append((ny, nx))

data[bg_mask, 3] = 0

# ── 第二步：边缘腐蚀 + 高斯羽化去除毛边 ───────────────────
alpha = data[:, :, 3].astype(float)
opaque_mask  = alpha > 10
eroded       = binary_erosion(opaque_mask, iterations=erosion_px)
alpha_smooth = gaussian_filter(alpha, sigma=1.0)

edge_zone = opaque_mask & ~eroded
alpha[edge_zone]    = alpha_smooth[edge_zone] * edge_opacity
alpha[~opaque_mask] = 0
alpha[eroded]       = data[:, :, 3][eroded]

data[:, :, 3] = np.clip(alpha, 0, 255).astype(np.uint8)

# ── 保存结果 ───────────────────────────────────────────────
result = Image.fromarray(data)
result.save(output_path)
print(f"已保存：{output_path}")
