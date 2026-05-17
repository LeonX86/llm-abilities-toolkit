import sys
import json
from pathlib import Path


def clean(content: str) -> str:
    # 去除 ASR 噪声：形如 "发言人 1 12:34" 的时间戳行
    cleaned_lines = []
    for line in content.split('\n'):
        parts = line.strip().split()
        if len(parts) == 3 and parts[0] == '发言人' and parts[1].isdigit() and ':' in parts[2]:
            continue
        cleaned_lines.append(line)
    return '\n'.join(cleaned_lines)


def parse(content: str) -> tuple[str, list[str]]:
    sections = content.split('## 会议转写')
    meeting_info = sections[0].strip()
    speeches = []
    if len(sections) > 1:
        for section in sections[1].split('###')[1:]:
            section = section.strip()
            if section:
                speeches.append('### ' + section)
    return meeting_info, speeches


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python prepare.py <meeting_transcript.md>', file=sys.stderr)
        sys.exit(1)

    input_path = Path(sys.argv[1]).resolve()
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    cleaned = clean(content)

    # 清洗后回写源文件，后续 get_section.py 可直接读取，无需重复清洗
    with open(input_path, 'w', encoding='utf-8') as f:
        f.write(cleaned)

    meeting_info, speeches = parse(cleaned)
    output_path = input_path.parent / f'{input_path.stem}_会议纪要.md'

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(meeting_info + '\n\n## 会议纪要\n')

    result = {
        'total': len(speeches),
        'source_path': str(input_path),
        'output_path': str(output_path),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
