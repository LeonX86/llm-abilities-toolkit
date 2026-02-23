import sys
from pathlib import Path

# 加载项目配置（绝对路径，确保 skill 部署到任何位置后仍可用）
sys.path.insert(0, str(Path.home() / 'WorkFlow/E/Code/Python/llm-abilities-toolkit'))
from config import config
from openai import OpenAI


PROMPT_TEMPLATE = """\
## 会议背景
{meeting_info}

## 本段转写原文
{speech}

## 任务
1. 先理解会议背景（主题、参与人、目的），再阅读转写原文
2. 结合会议背景修正 ASR 识别错误（人名、公司名、专业术语等）
3. 提取与会议主题相关的实质性观点，忽略口语废话、重复表达和无关闲谈
4. 保留原段落标题（### xxx）不变，在标题下方逐行列出观点
5. 每个观点使用数字序号（1. 2. 3.），语言简洁，使用书面语

## 输出格式
### 原段落标题
1. 观点一
2. 观点二

## 限制
1. 直接输出带标题的会议纪要内容，禁止输出其他任何无关内容。"""


def parse(content: str) -> tuple[str, list[str]]:
    cleaned_lines = []
    for line in content.split('\n'):
        parts = line.strip().split()
        if len(parts) == 3 and parts[0] == '发言人' and parts[1].isdigit() and ':' in parts[2]:
            continue
        cleaned_lines.append(line)

    cleaned = '\n'.join(cleaned_lines)
    sections = cleaned.split('## 会议转写')
    meeting_info = sections[0].strip()

    speeches = []
    if len(sections) > 1:
        for section in sections[1].split('###')[1:]:
            section = section.strip()
            if section:
                speeches.append('### ' + section)

    return meeting_info, speeches


def summarize(client: OpenAI, meeting_info: str, speech: str) -> str:
    prompt = PROMPT_TEMPLATE.format(speech=speech, meeting_info=meeting_info)
    response = client.chat.completions.create(
        model='Qwen/Qwen3-235B-A22B-Instruct-2507',
        messages=[
            {'role': 'system', 'content': '你是一位专业的会议纪要助手。'},
            {'role': 'user', 'content': prompt},
        ],
        stream=True,
    )
    result = []
    for chunk in response:
        if chunk.choices and chunk.choices[0].delta.content:
            text = chunk.choices[0].delta.content
            result.append(text)
            print(text, end='', flush=True)
    print()
    return ''.join(result)


def process(file_path: str) -> None:
    input_path = Path(file_path).resolve()
    with open(input_path, 'r', encoding='utf-8') as f:
        content = f.read()

    meeting_info, speeches = parse(content)
    total = len(speeches)
    print(f'共 {total} 段发言，开始逐段总结...\n')

    client = OpenAI(
        base_url=config.MODELSCOPE_BASE_URL,
        api_key=config.MODELSCOPE_API_KEY,
    )

    results = []
    for idx, speech in enumerate(speeches, 1):
        print(f'\r进度：{idx}/{total} 段', flush=True)
        print('-' * 40)
        summary = summarize(client, meeting_info, speech)
        results.append(summary)

    output_path = input_path.parent / f'{input_path.stem}_会议纪要.md'
    # meeting_info 对应模板中的 ## 基本信息 部分，放在 ## 会议纪要 之前
    sections = [meeting_info, '## 会议纪要'] + [r.strip() for r in results]
    final_content = '\n\n'.join(s for s in sections if s)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(final_content + '\n')

    print(f'\n已写入：{output_path}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print('用法: python process.py <会议转写.md>', file=sys.stderr)
        sys.exit(1)
    process(sys.argv[1])
