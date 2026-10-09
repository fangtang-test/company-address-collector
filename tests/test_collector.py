import json
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

import collector


ROOT = Path(__file__).resolve().parents[1]


class CollectorTests(unittest.TestCase):
    def test_address_list_with_comments_and_per_line_region(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as folder:
            self.assertTrue(Path(folder).resolve().is_relative_to(ROOT.resolve()))
            path = Path(folder) / 'input.txt'
            path.write_text('# 虚构清单\n示例路100号 | P1:100001\n\n示例街20号｜P2:100002\n', encoding='utf-8')
            self.assertEqual(collector.load_keys(path), [('示例路100号', 'P1:100001'), ('示例街20号', 'P2:100002')])

    def test_search_url_encodes_keyword_and_filters(self):
        with patch.object(collector, 'HOME_URL', 'https://example.invalid/'):
            url = collector.build_search_url('示例路100号', 'P1:100001,100002', '20,10')
        parts = urlsplit(url)
        self.assertEqual(parts.path, '/web/search')
        params = parse_qs(parts.query)
        self.assertEqual(params['key'], ['示例路100号'])
        filters = json.loads(params['filter'][0])
        self.assertEqual(filters['rchain'], [{'pr': 'P1', 'cc': [100001, 100002]}])
        self.assertEqual(filters['s'], ['20', '10'])

    def test_empty_status_does_not_add_filter(self):
        with patch.object(collector, 'HOME_URL', 'https://example.invalid/'):
            self.assertNotIn('filter', parse_qs(urlsplit(collector.build_search_url('示例路', '', '')).query))

    def test_public_template_requires_real_local_configuration(self):
        for url in ('', 'https://example.invalid/', 'https://name:password@example.org/', 'https://example.org/?token=sample'):
            with patch.object(collector, 'CONFIG_FILE', ROOT / 'nonexistent.example.json'):
                with self.assertRaises(ValueError):
                    collector.configure_site(url)

    def test_options_and_next_page(self):
        args = collector.parse_args(['--key', '示例路100号', '--max-pages=5', '--status=', '--only=存续,在业'])
        self.assertEqual(args['key'], '示例路100号')
        self.assertEqual(args['max_pages'], 5)
        self.assertEqual(args['status'], '')
        self.assertEqual(collector.bump_url_page('https://example.invalid/web/search?key=demo&p=2'), 'https://example.invalid/web/search?key=demo&p=3')

    def test_page_fixture_ignores_shareholder_and_non_card_links(self):
        with collector.sync_playwright() as p:
            browser = None
            for channel in ('msedge', 'chrome', None):
                try:
                    browser = p.chromium.launch(headless=True, channel=channel)
                    break
                except Exception:
                    if channel is None:
                        raise
            try:
                page = browser.new_page()
                page.set_content((ROOT / 'examples/search.example.html').read_text(encoding='utf-8'))
                rows = page.evaluate(collector.EXTRACT_JS)
            finally:
                browser.close()
        self.assertEqual([r['name'] for r in rows], ['示例企业甲（虚构）', '示例企业乙（虚构）'])
        self.assertEqual([r['status'] for r in rows], ['存续', '在业'])
        self.assertEqual(rows[0]['addr'], '示例市示例区示例路100号')


if __name__ == '__main__':
    unittest.main()
