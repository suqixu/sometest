"""导航生成脚本的文件操作回归测试。"""
import importlib.util
import json
from pathlib import Path
import re
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'update_navigation.py'
spec = importlib.util.spec_from_file_location('update_navigation', SCRIPT)
module = importlib.util.module_from_spec(spec)
if SCRIPT.exists():
    spec.loader.exec_module(module)
else:
    def missing_script(root):
        raise AssertionError('导航更新脚本尚未实现')
    module.update_navigation = missing_script

class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.nav = self.root / 'demo.html'
        self.nav.write_text('''<html><head><title>导航</title></head><body>
<small>1 个演示 · 同页预览</small><script>
const ITEMS = [{ file: 'old.html', title: '旧标题', tag: '模型 A', group: '自定义分组' }];
const groups = ['自定义分组'];
function hrefOf(file) { return encodeURIComponent(file); }
renderList();
select(ITEMS[0]);
</script></body></html>''', encoding='utf-8')
        (self.root / 'old.html').write_text('<html><head><title>页面标题</title></head><body><script>const keep = 1;</script></body></html>', encoding='utf-8')

    def items(self):
        content = self.nav.read_text(encoding='utf-8')
        return json.loads(re.search(r'const ITEMS = (\[.*?\]);', content, re.S)[1])

    def test_add_nested_rename_delete_and_keep_metadata(self):
        module.update_navigation(self.root)
        first = self.items()[0]
        self.assertEqual(first['tag'], '模型 A')
        self.assertEqual(first['group'], '自定义分组')
        sub = self.root / '子目录'
        sub.mkdir()
        (self.root / 'old.html').rename(sub / '改名.html')
        (sub / 'new.HTML').write_text('<title>新增 &amp; 页面</title>', encoding='utf-8')
        (self.root / '.hidden').mkdir()
        (self.root / '.hidden' / 'skip.html').write_text('<title>忽略</title>')
        module.update_navigation(self.root)
        items = {i['file']: i for i in self.items()}
        self.assertEqual(set(items), {'子目录/改名.html', '子目录/new.HTML'})
        self.assertEqual(items['子目录/改名.html']['tag'], '模型 A')
        self.assertEqual(items['子目录/new.HTML']['title'], '新增 & 页面')
        self.assertIn('2 个演示', self.nav.read_text())
        self.assertIn('const keep = 1;', (sub / '改名.html').read_text())
        (sub / '改名.html').unlink()
        module.update_navigation(self.root)
        self.assertEqual(len(self.items()), 1)

    def test_idempotent_and_directory_relocation(self):
        module.update_navigation(self.root)
        expected = self.nav.read_bytes()
        module.update_navigation(self.root)
        self.assertEqual(self.nav.read_bytes(), expected)
        moved = self.root.with_name(self.root.name + '-moved')
        self.root.rename(moved)
        self.addCleanup(lambda: __import__('shutil').rmtree(moved, ignore_errors=True))
        module.update_navigation(moved)
        self.assertEqual((moved / 'demo.html').read_bytes(), expected)

    def test_return_link_tracks_directory_depth_and_ui_selectors(self):
        page = self.root / 'old.html'
        page.write_text(r'''<head><title>页面</title></head><body>
<style>body > a[href="demo.html"] { min-height: 44px; }</style>
<a href="demo.html" target="_top">返回 Demo 导航</a>
<a href="https://example.com/demo.html">外部链接</a>
<script>document.querySelectorAll("body > a[href=\"demo.html\"]");</script>
</body>''', encoding='utf-8')
        sub = self.root / '第一层' / '第二层'
        sub.mkdir(parents=True)
        page.rename(sub / '改名.html')
        page = sub / '改名.html'
        module.update_navigation(self.root)
        content = page.read_text()
        self.assertIn('href="../../demo.html"', content)
        self.assertIn('data-demo-return', content)
        self.assertIn('body > a[data-demo-return]', content)
        self.assertNotIn('a[href="demo.html"]', content)
        self.assertIn('href="https://example.com/demo.html"', content)
        module.update_navigation(self.root)
        self.assertEqual(page.read_text(), content)
        page.rename(self.root / 'renamed.html')
        module.update_navigation(self.root)
        self.assertIn('href="demo.html"', (self.root / 'renamed.html').read_text())

    def test_empty_directory_is_safe(self):
        (self.root / 'old.html').unlink()
        module.update_navigation(self.root)
        self.assertEqual(self.items(), [])
        self.assertIn('if (ITEMS.length)', self.nav.read_text())

    def test_metadata_escaping_and_readonly_permission_preserved(self):
        page = self.root / 'old.html'
        page.write_text('''<head><title>安全标题</title><meta name="demo-title" content="&lt;/script&gt;&lt;b&gt;">
<meta name="demo-tag" content="自定义模型"><meta name="demo-group" content="自定义分类"></head>''', encoding='utf-8')
        page.chmod(0o444)
        module.update_navigation(self.root)
        self.assertEqual(page.stat().st_mode & 0o777, 0o444)
        item = self.items()[0]
        self.assertEqual(item['title'], '</script><b>')
        self.assertEqual(item['tag'], '自定义模型')
        self.assertNotIn('</script><b>', self.nav.read_text())

if __name__ == '__main__':
    unittest.main()
