import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import refresh


class RefreshTests(unittest.TestCase):
    def test_discovery_uses_homepage_and_stable_repository_key(self):
        home = '<script id="nodes-data">{"meta":{"subBase":"https://downloads.example/v2/"}}</script>clash: {file:"latest.yaml"}'
        index = '<a href="/sources/renamed-slug/">owner/Repo</a>'
        url, hosts, sources = refresh.discover(home, index)
        self.assertEqual(url, 'https://downloads.example/v2/latest.yaml')
        self.assertIn('downloads.example', hosts)
        self.assertEqual(sources['owner-repo'][1], 'https://nodesradar.com/sources/renamed-slug/')

    def test_bad_update_preserves_previous_file_and_index_entry(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            old = b'vless://id@server.example:443\n'
            previous = {'name': 'owner/repo', 'path': 'by-source/owner-repo.txt', 'sha256': hashlib.sha256(old).hexdigest()}
            refresh.atomic(out / previous['path'], old)
            refresh.atomic(out / 'index.json', json.dumps({'feeds': {'owner-repo': previous}}).encode())
            good = b'proxies: [{name: node, type: ss, server: example.com, port: 443}]\n'
            with patch.object(refresh, 'OUT', out), patch.object(refresh, 'discover', return_value=(
                'https://downloads.example/new.yaml', {'downloads.example'}, {'owner-repo': ('owner/repo', 'https://nodesradar.com/sources/repo/')})), \
                 patch.object(refresh, 'source_url', return_value='https://downloads.example/bad.txt'), \
                 patch.object(refresh, 'fetch', side_effect=lambda url, _: good if url.endswith('new.yaml') else b'<html>error</html>'):
                refresh.main()
            self.assertEqual((out / previous['path']).read_bytes(), old)
            self.assertEqual(json.loads((out / 'index.json').read_text('utf-8'))['feeds']['owner-repo'], previous)
            self.assertEqual((out / 'clash.yaml').read_bytes(), good)


if __name__ == '__main__':
    unittest.main()
