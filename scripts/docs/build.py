"""从维护说明 Markdown 源生成网页；只在更新说明时运行，需要本机 markdown 包。"""
from pathlib import Path
import markdown

ROOT = Path(__file__).resolve().parents[2]
sections = [
    ('使用方法.md', 'usage'), ('operations.md', 'operations'),
    ('分级与清理.md', 'policy'), ('免费来源核验.md', 'sources'),
]
parts = []
for name, anchor in sections:
    source = ROOT / 'references' / name
    if source.exists():
        content = source.read_text().replace('(使用方法.md)', '(#usage)')
        parts.append(f'<a id="{anchor}"></a>\n\n' + content)
text = '\n\n---\n\n'.join(parts)
body = markdown.markdown(text, extensions=['tables', 'fenced_code'])
page = '''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>通用代理池 · 使用说明</title><link rel="stylesheet" href="/assets/guide.css">
</head><body><nav><a href="/">← 返回代理池看板</a><span>采集代理池 / 使用说明</span></nav>
<main>''' + body + '</main></body></html>\n'
(ROOT / 'web/guide.html').write_text(page)
print('已更新 web/guide.html')
