import sys
from pathlib import Path


def validate(content: str, file_path: Path) -> None:
    if file_path.suffix.lower() != '.md':
        print(f'错误：文件必须为 .md 格式，当前为 {file_path.suffix}', file=sys.stderr)
        sys.exit(1)
    if '## 基本信息' not in content:
        print('错误：缺少"## 基本信息"章节', file=sys.stderr)
        sys.exit(1)
    if '## 会议转写' not in content:
        print('错误：缺少"## 会议转写"章节', file=sys.stderr)
        sys.exit(1)
    after_transcript = content.split('## 会议转写', 1)[-1]
    if '### ' not in after_transcript:
        print('错误："## 会议转写"下未找到任何段落（### xxx）', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python validate.py <meeting_transcript.md>', file=sys.stderr)
        sys.exit(1)
    path = Path(sys.argv[1]).resolve()
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    validate(content, path)
    print('校验通过')
