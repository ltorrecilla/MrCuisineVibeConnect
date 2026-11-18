import copy
import json
import tempfile
import unittest
from unittest.mock import patch, MagicMock
from urllib.error import HTTPError
from pathlib import Path
from urllib.parse import urlsplit

import download


class FakeClient:
    def __init__(self):
        self.calls = []
        self.ids = [1, 2, 3]
        self.fail = set()

    def get(self, url, image=False):
        self.calls.append(url)
        path = urlsplit(url).path
        if '/page/' in path:
            page = int(path.split('/page/')[1].split('/')[0])
            entries = [{'id': n, 'lastUpdated': '2026-01-01'} for n in self.ids]
            return json.dumps({'data': {'total': len(entries), 'totalPage': (len(entries) + 1) // 2,
                                       'recipes': entries[(page-1)*2:page*2]}}).encode()
        key = int(path.rsplit('/', 1)[1])
        if key in self.fail:
            raise OSError('simulated failure')
        return json.dumps({'data': {'recipe': {'id': key, 'name': 'Soupe',
            'servingSizes': [{'amount': 4, 'steps': [{'deviceSetting': {
                'mode': 'customized', 'settings': [{'time': 45, 'speedFloat': 2.5, 'temperature': 95}]}}]},
                {'amount': 8, 'steps': []}], 'extraField': 'preserved'}}}, indent=3).encode()


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = download.parser().parse_args(['--output', str(self.root), '--test-count', '2'])
        self.client = FakeClient()

    def run_archive(self):
        return download.archive(self.args, self.client)

    def manifest(self):
        return json.loads((self.root / 'fr-FR/manifest.json').read_text())

    def test_sample_then_full_resume_and_corruption_repair(self):
        self.assertEqual(self.run_archive(), 0)
        self.assertEqual(set(self.manifest()['recipes']), {'1', '2'})
        self.client.calls.clear()
        self.args.mode = 'full'
        self.assertEqual(self.run_archive(), 0)
        details = [u for u in self.client.calls if '/page/' not in u]
        self.assertEqual(details, [download.API + '/api/v2/recipes/3'])
        path = self.root / 'fr-FR/recipes/1.json'
        path.write_text('damaged')
        self.client.calls.clear()
        self.run_archive()
        self.assertIn(download.API + '/api/v2/recipes/1', self.client.calls)

    def test_original_bytes_and_all_servings_preserved(self):
        self.run_archive()
        raw = (self.root / 'fr-FR/recipes/1.json').read_bytes()
        self.assertEqual(raw, self.client.get(download.API + '/api/v2/recipes/1'))
        self.assertEqual(len(json.loads(raw)['data']['recipe']['servingSizes']), 2)

    def test_failed_detail_retried(self):
        self.client.fail = {2}
        self.assertEqual(self.run_archive(), 1)
        self.assertFalse(self.manifest()['last_run']['complete'])
        self.client.fail.clear()
        self.assertEqual(self.run_archive(), 0)
        self.assertNotIn('error', self.manifest()['recipes']['2'])

    def test_full_marks_removed_but_test_does_not(self):
        self.args.mode = 'full'
        self.run_archive()
        self.client.ids = [1, 2]
        self.args.mode = 'test'
        self.run_archive()
        self.assertTrue(self.manifest()['recipes']['3']['available'])
        self.args.mode = 'full'
        self.run_archive()
        self.assertFalse(self.manifest()['recipes']['3']['available'])
        self.assertTrue((self.root / 'fr-FR/recipes/3.json').exists())

    def test_duplicate_pagination_rejected_without_manifest_changes(self):
        self.args.mode = 'full'
        self.run_archive()
        before = copy.deepcopy(self.manifest())
        self.client.ids = [1, 2, 2]
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            self.run_archive()
        self.assertEqual(self.manifest(), before)

    def test_mismatched_detail_is_not_saved(self):
        original = self.client.get
        def wrong(url, image=False):
            if '/page/' not in url:
                return original(download.API + '/api/v2/recipes/99')
            return original(url)
        self.client.get = wrong
        self.assertEqual(self.run_archive(), 1)
        self.assertFalse((self.root / 'fr-FR/recipes/1.json').exists())


class NetworkTests(unittest.TestCase):
    def response(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.status = 200
        response.read.return_value = b'{}'
        return response

    @patch('download.time.sleep')
    @patch('download.urlopen')
    def test_rate_limit_respects_retry_after(self, opening, sleeping):
        opening.side_effect = [HTTPError('https://example.com', 429, 'slow',
                                        {'Retry-After': '12'}, None), self.response()]
        self.assertEqual(download.Client().get('https://example.com'), b'{}')
        self.assertIn(unittest.mock.call(12.0), sleeping.call_args_list)
        self.assertEqual(opening.call_count, 2)

    @patch('download.time.sleep')
    @patch('download.urlopen')
    def test_auth_error_not_retried(self, opening, sleeping):
        opening.side_effect = HTTPError('https://example.com', 403, 'denied', {}, None)
        with self.assertRaises(HTTPError):
            download.Client().get('https://example.com')
        self.assertEqual(opening.call_count, 1)


if __name__ == '__main__':
    unittest.main()
