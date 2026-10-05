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
        self.assertEqual(json.loads(report)['app_version'], '2.0')


REPO = Path(__file__).resolve().parents[1]


class AttributionTests(unittest.TestCase):
    """署名与第三方组件说明必须随包附带，不能被悄悄改掉。"""

    def text(self, name):
        return (REPO / name).read_text(encoding='utf-8-sig')

    def test_author_is_credited_in_shipped_files(self):
        for name in ('LICENSE.txt', 'THIRD_PARTY.txt', 'README.md'):
            self.assertIn('lin1024-star', self.text(name), name)

    def test_vocal_separation_model_is_attributed_to_uvr(self):
        for name in ('THIRD_PARTY.txt', 'payload/关于本软件.txt'):
            text = self.text(name)
            self.assertIn('Kim_Vocal_2', text, name)
            self.assertIn('Ultimate Vocal Remover', text, name)
            self.assertIn('ultimatevocalremovergui', text, name)

    def test_guide_generator_carries_the_attribution(self):
        """小白指南是朋友最先读的文档，署名必须写进生成脚本，而不是只躺在 THIRD_PARTY.txt 里。"""
        text = self.text('source/make_guide.py')
        self.assertIn('lin1024-star', text)
        self.assertIn('Ultimate Vocal Remover', text)
        self.assertIn('Kim_Vocal_2', text)

    def test_every_shipped_guide_credits_author_and_uvr(self):
        for name in ('小白指南.html', 'payload/guide.html',
                     '先读我_快速开始.txt', '常见问题与解决方法.txt'):
            text = self.text(name)
            self.assertIn('lin1024-star', text, name)
            self.assertIn('Ultimate Vocal Remover', text, name)
            self.assertIn('Kim_Vocal_2', text, name)

    def test_in_app_about_text_states_author_and_free_use(self):
        about = self.text('payload/关于本软件.txt')
        self.assertIn('lin1024-star', about)
        self.assertIn('心非', about)
        self.assertIn('不收费', about)

    def test_about_text_is_reachable_from_the_window(self):
        app = self.text('payload/app.py')
        self.assertIn('def open_about', app)
        self.assertIn('self.open_about()', app)
        self.assertIn('关于与署名', app)


if __name__ == '__main__':
    unittest.main()