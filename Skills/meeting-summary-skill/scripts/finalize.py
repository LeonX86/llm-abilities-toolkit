import sys
import re
from pathlib import Path


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: python finalize.py <output_path> <total>', file=sys.stderr)
        sys.exit(1)

    output_path = Path(sys.argv[1]).resolve()
    total = int(sys.argv[2])

    # 查找所有临时文件
    temp_files = list(output_path.parent.glob(f'{output_path.stem}_temp_*.md'))

    def extract_index(f: Path) -> int:
        m = re.search(r'_temp_(\d+)\.md$', f.name)
        return int(m.group(1)) if m else -1

    if len(temp_files) != total:
        found_indices = [extract_index(f) for f in temp_files]
        missing = [i for i in range(1, total + 1) if i not in found_indices]
        print(f'错误：期望 {total} 个临时文件，实际找到 {len(temp_files)} 个，缺失段落：{missing}', file=sys.stderr)
        sys.exit(1)

    temp_files.sort(key=extract_index)

    # 读取 output_path 现有内容（基本信息 + ## 会议纪要 标题）
    with open(output_path, 'r', encoding='utf-8') as f:
        header = f.read()

    # 按顺序读取各临时文件并合并
    sections = []
    for tf in temp_files:
        with open(tf, 'r', encoding='utf-8') as f:
            sections.append(f.read().strip())

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(header.rstrip() + '\n\n' + '\n\n'.join(sections) + '\n')

    # 清理所有临时文件
    for tf in temp_files:
        tf.unlink()

    print(f'已完成：{total} 段按顺序合并至 {output_path}，临时文件已清理')
