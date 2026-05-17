import sys
from pathlib import Path


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print('Usage: python write_section.py <output_path> <summary>', file=sys.stderr)
        sys.exit(1)

    output_path = Path(sys.argv[1]).resolve()
    summary = sys.argv[2].strip().replace('\\n', '\n')

    with open(output_path, 'a', encoding='utf-8') as f:
        f.write('\n\n' + summary)

    print(f'Appended section to: {output_path}')
