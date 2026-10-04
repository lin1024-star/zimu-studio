import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import core
import easy_runtime


class EasyInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def prepare(self, alias='small'):
        folder = self.root / 'prepared' / alias
        folder.mkdir(parents=True)
        for name in ('model.bin', 'config.json', 'tokenizer.json', 'ready.json'):
            (folder / name).write_text('fixture', encoding='utf-8')
        return folder

    def test_verified_installed_model_resolves_offline(self):
        for alias in ('small', 'turbo'):
            folder = self.prepare(alias)
            self.assertEqual(core.prepared_model_path(alias, self.root), str(folder))

    def test_incomplete_install_cannot_be_used_as_model(self):
        folder = self.prepare()
        for name in ('ready.json', 'tokenizer.json', 'model.bin', 'config.json'):
            p = folder / name
            data = p.read_bytes()
            p.unlink()
            self.assertEqual(core.prepared_model_path('small', self.root), 'small')
            p.write_bytes(data)

    def test_manual_model_paths_and_other_aliases_remain_unchanged(self):
        self.prepare()
        for name in ('medium', '../small', '/custom/model', r'C:\models\custom'):
            self.assertEqual(core.prepared_model_path(name, self.root), name)

    def test_first_install_defaults_without_credentials(self):
        with patch.object(easy_runtime, 'ROOT', self.root), patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'sk-QA-CANARY'}):
            easy_runtime.initialize_settings('small')
        text = (self.root / 'settings.json').read_text('utf-8')
        data = json.loads(text)
        self.assertEqual(data['asr_model'], 'small')
        self.assertTrue(data['device'].startswith('CPU'))
        self.assertNotIn('sk-QA-CANARY', text)
        self.assertNotIn('api_key', data)

    def test_install_repair_preserves_existing_settings(self):
        before = b'{"model":"chosen-model","output":"user-folder","device":"CPU"}'
        p = self.root / 'settings.json'
        p.write_bytes(before)
        with patch.object(easy_runtime, 'ROOT', self.root):
            easy_runtime.initialize_settings('turbo', gpu=True)
        self.assertEqual(p.read_bytes(), before)

    def test_srt_install_does_not_persist_invalid_model_alias(self):
        with patch.object(easy_runtime, 'ROOT', self.root):
            easy_runtime.initialize_settings('srt')
        self.assertEqual(json.loads((self.root / 'settings.json').read_text(encoding='utf-8'))['asr_model'], 'small')

    def test_installer_diagnostics_exclude_user_settings_and_secrets(self):
        (self.root / 'settings.json').write_text('{"transcript":"PRIVATE_SOURCE","api_key":"sk-CANARY"}')
        stream = io.StringIO()
        with patch.object(easy_runtime, 'ROOT', self.root), patch('sys.stdout', stream):
            easy_runtime.diagnose()
        report = stream.getvalue()
        self.assertNotIn('PRIVATE_SOURCE', report)
        self.assertNotIn('sk-CANARY', report)
        self.assertNotIn(str(self.root), report)
        self.assertEqual(json.loads(report)['app_version'], '1.6.0')


if __name__ == '__main__':
    unittest.main()
