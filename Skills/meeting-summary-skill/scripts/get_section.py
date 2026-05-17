import sys
import json
from pathlib import Path


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: python get_section.py <section_index> <meeting_transcript.md>', file=sys.stderr)
        sys.exit(1)

    section_index = int(sys.argv[1])
    input_path = Path(sys.argv[2]).resolve()

    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # 源文件已由 prepare.py 完成清洗，此处只做分段
    parts = content.split('## 会议转写')
    meeting_info = parts[0].strip()
    speeches = []
    if len(parts) > 1:
        for section in parts[1].split('###')[1:]:
            section = section.strip()
            if section:
                speeches.append('### ' + section)

    if section_index < 1 or section_index > len(speeches):
        print(f'错误：section_index {section_index} 超出范围（1-{len(speeches)}）', file=sys.stderr)
        sys.exit(1)

    result = {
        'section_index': section_index,
        'total': len(speeches),
        'meeting_info': meeting_info,
        'speech': speeches[section_index - 1],
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
