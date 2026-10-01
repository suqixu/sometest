#!/usr/bin/env python3
"""扫描脚本所在目录及子目录，一键更新 demo.html（仅使用 Python 标准库）。

直接运行：python3 update_navigation.py
自定义标签：<meta name="demo-tag" content="模型名称">
自定义分类：<meta name="demo-group" content="分类名称">
可选标题：<meta name="demo-title" content="导航标题">
文件改名、移动后重新运行即可。隐藏目录、符号链接和 node_modules 不扫描。
"""
import argparse
import ast
from html import escape
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import quote, urlsplit

ITEMS_PATTERN = re.compile(r'const ITEMS\s*=\s*(\[.*?\]);', re.S)


class PageMetadata(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.metadata = {}
        self.title_parts = []
        self.in_title = False
        self.feed(re.split(r'</head\s*>', source, maxsplit=1, flags=re.I)[0])

    def handle_starttag(self, tag, attrs):
        if tag == 'title':
            self.in_title = True
        elif tag == 'meta':
            attrs = dict(attrs)
            name = (attrs.get('name') or '').lower()
            if name.startswith('demo-'):
                self.metadata[name] = attrs.get('content') or ''

    def handle_endtag(self, tag):
        if tag == 'title':
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title_parts.append(data)

    @property
    def title(self):
        return ''.join(self.title_parts).strip()


def read_existing_items(source):
    match = ITEMS_PATTERN.search(source)
    if not match:
        raise ValueError('demo.html 中未找到 const ITEMS，未修改任何文件。')
    try:
        return json.loads(match[1])
    except json.JSONDecodeError:
        # 兼容最初的 JavaScript 单引号列表；只解析字符串，绝不执行代码。
        string = r"('(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\")"
        result = []
        for entry in re.findall(r'\{([^{}]*)\}', match[1], re.S):
            item = {}
            for key in ('file', 'title', 'tag', 'group'):
                value = re.search(r'\b' + key + r'\s*:\s*' + string, entry)
                if not value:
                    raise ValueError('无法解析现有导航条目，未修改任何文件。')
                item[key] = ast.literal_eval(value[1])
            result.append(item)
        if not result and match[1].strip() != '[]':
            raise ValueError('无法解析现有导航列表，未修改任何文件。')
        return result


def write_preserving_permissions(path, content):
    if path.read_text(encoding='utf-8') == content:
        return
    mode = path.stat().st_mode
    try:
        path.chmod(mode | 0o200)
        path.write_text(content, encoding='utf-8')
    finally:
        path.chmod(mode)


def add_metadata(source, metadata):
    lines = '\n'.join(
        f'<meta name="{name}" content="{escape(str(value), quote=True)}">'
        for name, value in metadata.items()
    )
    if not lines:
        return source
    head = re.search(r'<head\b[^>]*>', source, re.I)
    if head:
        return source[:head.end()] + '\n' + lines + source[head.end():]
    # 简化 HTML 也可使用元信息；HTML 解析器会将它归入 head。
    doctype = re.match(r'\s*<!doctype[^>]*>', source, re.I)
    offset = doctype.end() if doctype else 0
    return source[:offset] + '\n' + lines + '\n' + source[offset:]


def update_return_links(source, page, navigation):
    """修复现有本地返回链接，并让 UI 选择器与链接地址解耦。"""
    relative = quote(Path(os.path.relpath(navigation, page.parent)).as_posix(), safe='/')
    offsets = [0]
    for line in source.splitlines(keepends=True):
        offsets.append(offsets[-1] + len(line))

    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.replacements = []

        def handle_starttag(self, tag, attrs):
            if tag != 'a':
                return
            attrs = dict(attrs)
            href = attrs.get('href') or ''
            url = urlsplit(href)
            managed = 'data-demo-return' in attrs
            local_demo = (not url.scheme and not url.netloc
                          and url.path.rsplit('/', 1)[-1] == 'demo.html')
            if not managed and not local_demo:
                return
            original = self.get_starttag_text()
            updated = re.sub(r'''\bhref\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+)''',
                             lambda match: f'href="{relative}"', original, count=1, flags=re.I)
            if 'href' not in attrs:
                updated = updated[:-1] + f' href="{relative}">'
            if not managed:
                updated = updated[:-1] + ' data-demo-return>'
            line, column = self.getpos()
            start = offsets[line - 1] + column
            self.replacements.append((start, start + len(original), updated))

    parser = Links()
    parser.feed(source)
    if not parser.replacements:
        return source
    for start, end, updated in reversed(parser.replacements):
        source = source[:start] + updated + source[end:]
    return (source.replace('a[href="demo.html"]', 'a[data-demo-return]')
            .replace(r'a[href=\"demo.html\"]', 'a[data-demo-return]'))


def update_navigation(root):
    root = Path(root).resolve()
    navigation = root / 'demo.html'
    source = navigation.read_text(encoding='utf-8')
    existing = read_existing_items(source)
    old_by_file = {item['file']: (index, item) for index, item in enumerate(existing)}
    candidates = sorted(
        (path for path in root.rglob('*')
         if path.is_file() and path.suffix.lower() == '.html'
         and path != navigation
         and not any(part.startswith('.') or part == 'node_modules'
                     for part in path.relative_to(root).parts)
         and not any(parent.is_symlink() for parent in [path, *path.parents])),
        key=lambda path: path.relative_to(root).as_posix().casefold(),
    )
    items = []
    updates = []
    for index, path in enumerate(candidates):
        relative = path.relative_to(root).as_posix()
        old_index, old = old_by_file.get(relative, (len(existing) + index, {}))
        page_source = path.read_text(encoding='utf-8')
        parsed = PageMetadata(page_source)
        meta = parsed.metadata
        item = {
            'file': relative,
            'title': meta.get('demo-title') or parsed.title or path.stem,
            'tag': meta.get('demo-tag') or old.get('tag') or 'HTML 演示',
            'group': meta.get('demo-group') or old.get('group') or '其他演示',
        }
        try:
            order = int(meta.get('demo-order', old_index))
        except ValueError:
            order = old_index
        items.append((order, item))
        missing = {name: value for name, value in {
            'demo-tag': item['tag'], 'demo-group': item['group'], 'demo-order': order,
        }.items() if not meta.get(name)}
        updated = add_metadata(page_source, missing)
        updates.append((path, update_return_links(updated, path, navigation)))
    items.sort(key=lambda entry: (entry[0], entry[1]['file'].casefold()))
    entries = [item for _, item in items]
    serialized = json.dumps(entries, ensure_ascii=False, indent=2)
    # 防止标题或文件名中的 HTML 结束标签提前终止内联脚本。
    for char, escaped in [('<', r'\u003c'), ('>', r'\u003e'), ('&', r'\u0026'),
                          ('\u2028', r'\u2028'), ('\u2029', r'\u2029')]:
        serialized = serialized.replace(char, escaped)
    source = ITEMS_PATTERN.sub(lambda match: 'const ITEMS = ' + serialized + ';', source, count=1)
    source = re.sub(r'const groups\s*=\s*\[.*?\];',
                    'const groups = [...new Set(ITEMS.map(item => item.group))];', source, count=1, flags=re.S)
    source = source.replace('return encodeURIComponent(file);',
                            "return file.split('/').map(encodeURIComponent).join('/');")
    source = source.replace('${groupEmoji[g]} ${g}', "${groupEmoji[g] || '📄'} ${g}")
    source = re.sub(r'\d+ 个演示 · 同页预览', f'{len(entries)} 个演示 · 同页预览', source)
    source = source.replace('\nselect(ITEMS[0]);', '\nif (ITEMS.length) select(ITEMS[0]);')
    for path, updated in updates:
        write_preserving_permissions(path, updated)
    write_preserving_permissions(navigation, source)
    return len(entries)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--directory', type=Path, default=Path(__file__).resolve().parent,
                        help='可选：指定包含 demo.html 的目录')
    args = parser.parse_args()
    try:
        count = update_navigation(args.directory)
    except (OSError, ValueError) as error:
        print(f'更新失败：{error}', file=sys.stderr)
        return 1
    print(f'更新完成：{count} 个演示。重新打开或刷新 demo.html 即可。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
