import sys
from pathlib import Path


def validate(content: str, file_path: Path) -> None:
    if file_path.suffix.lower() != '.md':
        print(f'错误：文件必须是 .md 格式，当前为 {file_path.suffix}', file=sys.stderr)
        sys.exit(1)
    if '## 基本信息' not in content:
        print('错误：文件缺少 "## 基本信息" 段落', file=sys.stderr)
        sys.exit(1)
    if '## 会议转写' not in content:
        print('错误：文件缺少 "## 会议转写" 段落', file=sys.stderr)
        sys.exit(1)
    after_transcript = content.split('## 会议转写', 1)[-1]
    if '### ' not in after_transcript:
        print('错误："## 会议转写" 之后没有找到任何发言段落（### xxx）', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python validate.py <会议转写.md>', file=sys.stderr)
        sys.exit(1)
    path = Path(sys.argv[1]).resolve()
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    validate(content, path)
    print('校验通过')
