import sys
from pathlib import Path


def validate(content: str, file_path: Path) -> None:
    if file_path.suffix.lower() != '.md':
        print(f'Error: file must be .md format, got {file_path.suffix}', file=sys.stderr)
        sys.exit(1)
    if '## 基本信息' not in content:
        print('Error: missing "## Basic Info" section', file=sys.stderr)
        sys.exit(1)
    if '## 会议转写' not in content:
        print('Error: missing "## Meeting Transcript" section', file=sys.stderr)
        sys.exit(1)
    after_transcript = content.split('## 会议转写', 1)[-1]
    if '### ' not in after_transcript:
        print('Error: no speech sections (### xxx) found after "## Meeting Transcript"', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('Usage: python validate.py <meeting_transcript.md>', file=sys.stderr)
        sys.exit(1)
    path = Path(sys.argv[1]).resolve()
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    validate(content, path)
    print('Validation passed')
