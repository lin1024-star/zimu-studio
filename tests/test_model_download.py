import sys
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import app
import core


def make_model_folder(root, name, files=("model.bin", "config.json", "tokenizer.json")):
    folder = Path(root) / name
    folder.mkdir(parents=True, exist_ok=True)
    for item in files:
        (folder / item).write_bytes(b"x")
    return folder


class LocalModelStatusTest(unittest.TestCase):
    def test_nothing_installed(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(core.local_model_status("large-v3", tmp), "")

    def test_installer_prepared_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_model_folder(tmp, "prepared/small", ("model.bin", "config.json", "tokenizer.json", "ready.json"))
            self.assertEqual(core.local_model_status("small", tmp), "prepared")

    def test_program_downloaded_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_model_folder(tmp, "downloaded/large-v3")
            self.assertEqual(core.local_model_status("large-v3", tmp), "downloaded")

    def test_incomplete_download_is_not_ready(self):
        # 中断在 model.bin 之前：只有 config.json 不算下好，否则会拿半个模型去识别。
        with tempfile.TemporaryDirectory() as tmp:
            make_model_folder(tmp, "downloaded/medium", ("config.json",))
            self.assertEqual(core.local_model_status("medium", tmp), "")

    def test_unknown_name_is_never_local(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(core.local_model_status("D:/my-model", tmp), "")


class PreparedModelPathTest(unittest.TestCase):
    def test_prepared_folder_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = make_model_folder(tmp, "prepared/turbo", ("model.bin", "config.json", "tokenizer.json", "ready.json"))
            self.assertEqual(core.prepared_model_path("turbo", tmp), str(folder))

    def test_downloaded_folder_is_used(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = make_model_folder(tmp, "downloaded/large-v3")
            self.assertEqual(core.prepared_model_path("large-v3", tmp), str(folder))

    def test_missing_model_returns_the_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(core.prepared_model_path("medium", tmp), "medium")

    def test_user_supplied_path_is_untouched(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(core.prepared_model_path("D:/models/mine", tmp), "D:/models/mine")

    def test_every_builtin_name_has_a_repo(self):
        for name in ("tiny", "base", "small", "medium", "large-v3", "turbo"):
            self.assertIn(name, core.BUILTIN_MODEL_REPOS)
            self.assertTrue(core.model_download_mb(name) > 0)


class DownloadRouteTest(unittest.TestCase):
    def test_mirror_is_preferred_when_reachable(self):
        seen = []
        route = core.choose_download_route("Systran/faster-whisper-large-v3",
                                           probe=lambda url, prox: seen.append(url) or True)
        self.assertEqual(route, (core.HF_MIRROR, True))
        self.assertTrue(seen[0].startswith(core.HF_MIRROR))

    def test_broken_proxy_falls_back_to_direct(self):
        # 代理软件没开、端口变了：走代理必失败，不该让人白等。
        calls = []

        def probe(url, use_proxy):
            calls.append(use_proxy)
            return not use_proxy

        route = core.choose_download_route("Systran/faster-whisper-large-v3", probe=probe)
        self.assertEqual(route, (core.HF_MIRROR, False))
        self.assertEqual(calls[:2], [True, False])

    def test_official_site_is_tried_before_giving_up(self):
        calls = []

        def probe(url, use_proxy):
            calls.append((url.startswith(core.HF_MIRROR), use_proxy))
            return url.startswith(core.HF_OFFICIAL) and not use_proxy

        route = core.choose_download_route("Systran/faster-whisper-small", probe=probe)
        self.assertEqual(route, (core.HF_OFFICIAL, False))
        self.assertEqual(len(calls), 4)

    def test_nothing_reachable_still_picks_the_mirror_directly(self):
        route = core.choose_download_route("Systran/faster-whisper-small", probe=lambda *a: False)
        self.assertEqual(route, (core.HF_MIRROR, False))

    def test_route_is_logged(self):
        lines = []
        core.choose_download_route("Systran/faster-whisper-small",
                                   emit=lambda kind, value: lines.append(value),
                                   probe=lambda *a: True)
        self.assertTrue(any("国内镜像" in line for line in lines))


class ApplyRouteTest(unittest.TestCase):
    def setUp(self):
        self.saved = {name: core.os.environ.get(name) for name in core._PROXY_ENV + ("HF_ENDPOINT", "NO_PROXY")}

    def tearDown(self):
        for name, value in self.saved.items():
            if value is None:
                core.os.environ.pop(name, None)
            else:
                core.os.environ[name] = value

    def test_mirror_endpoint_is_set(self):
        core.apply_download_route(core.HF_MIRROR, True)
        self.assertEqual(core.os.environ["HF_ENDPOINT"], core.HF_MIRROR)

    def test_xet_is_disabled_so_files_really_come_from_the_mirror(self):
        # 实测教训：不关 Xet，文件本体仍走 cas-server.xethub.hf.co，返回 401。
        # 光设 HF_ENDPOINT 是不够的。
        core.apply_download_route(core.HF_MIRROR, True)
        self.assertEqual(core.os.environ["HF_HUB_DISABLE_XET"], "1")

    def test_proxy_is_kept_when_it_works(self):
        core.os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
        core.apply_download_route(core.HF_MIRROR, True)
        self.assertEqual(core.os.environ["HTTPS_PROXY"], "http://127.0.0.1:7890")

    def test_dead_proxy_is_removed(self):
        core.os.environ["HTTPS_PROXY"] = "http://127.0.0.1:7890"
        core.os.environ["ALL_PROXY"] = "socks5://127.0.0.1:7891"
        core.apply_download_route(core.HF_MIRROR, False)
        self.assertNotIn("HTTPS_PROXY", core.os.environ)
        self.assertNotIn("ALL_PROXY", core.os.environ)
        self.assertEqual(core.os.environ["NO_PROXY"], "*")


class DownloadWatcherTest(unittest.TestCase):
    def test_progress_follows_the_folder_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            events = []
            watcher = core._DownloadWatcher(tmp, 10, lambda kind, value: events.append((kind, value)))
            (Path(tmp) / "model.bin").write_bytes(b"x" * (5 * 1048576))
            watcher.report()
            progress = [value for kind, value in events if kind == "progress"]
            self.assertEqual(len(progress), 1)
            self.assertAlmostEqual(progress[0], 50.0, places=1)
            self.assertTrue(any("5 / 约 10 MB" in value for kind, value in events if kind == "status"))


class DownloadBuiltinModelTest(unittest.TestCase):
    def test_unknown_model_is_rejected_before_any_network_use(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(core.UserError):
                core.download_builtin_model("gigantic", tmp, lambda *a: None)

    def test_failure_message_tells_a_non_technical_user_what_to_do(self):
        hint = core.model_download_hint("large-v3", 3090, "D:/models/downloaded/large-v3")
        self.assertIn("3090", hint)
        self.assertIn("small 或 turbo", hint)
        self.assertIn("D:/models/downloaded/large-v3", hint)


class PrepareModelTest(unittest.TestCase):
    def test_existing_model_is_not_downloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            make_model_folder(tmp, "prepared/turbo", ("model.bin", "config.json", "tokenizer.json", "ready.json"))
            with patch.object(core, "download_builtin_model") as fetch, \
                 patch.object(core, "download_from_modelscope") as ms:
                path = core.prepare_model("turbo", tmp, lambda *a: None)
            fetch.assert_not_called()
            ms.assert_not_called()
            self.assertEqual(path, str(Path(tmp) / "prepared" / "turbo"))

    def test_modelscope_is_preferred_because_it_is_much_faster(self):
        # 国内实测：魔搭 14.8 MB/s，hf-mirror 20 MB 跑 120 秒都没下完。
        with tempfile.TemporaryDirectory() as tmp:
            dest = core.downloaded_model_dir("large-v3", tmp)

            def fake_download(repo, folder, emit, stop, total_mb):
                Path(folder).mkdir(parents=True, exist_ok=True)
                for name in core._MODEL_REQUIRED:
                    (Path(folder) / name).write_bytes(b"x")

            with patch.object(core, "modelscope_ready", return_value=True), \
                 patch.object(core, "download_from_modelscope", side_effect=fake_download) as ms, \
                 patch.object(core, "download_builtin_model") as fetch:
                path = core.prepare_model("large-v3", tmp, lambda *a: None)
            ms.assert_called_once()
            fetch.assert_not_called()
            self.assertEqual(path, str(dest))

    def test_falls_back_to_hugging_face_when_modelscope_lacks_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(core, "modelscope_ready", return_value=False), \
                 patch.object(core, "download_builtin_model", return_value="ok") as fetch:
                path = core.prepare_model("large-v3", tmp, lambda *a: None)
            fetch.assert_called_once()
            self.assertEqual(path, "ok")

    def test_half_finished_modelscope_download_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(core, "modelscope_ready", return_value=True), \
                 patch.object(core, "download_from_modelscope", return_value=10):
                with self.assertRaises(core.UserError):
                    core.prepare_model("large-v3", tmp, lambda *a: None)

    def test_user_supplied_path_is_never_downloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(core, "download_builtin_model") as fetch, \
                 patch.object(core, "download_from_modelscope") as ms:
                path = core.prepare_model("D:/models/mine", tmp, lambda *a: None)
            fetch.assert_not_called()
            ms.assert_not_called()
            self.assertEqual(path, "D:/models/mine")


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload, self.status, self.pos = payload, status, 0

    def read(self, size=-1):
        chunk = self.payload[self.pos:self.pos + (size if size > 0 else len(self.payload))]
        self.pos += len(chunk)
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, response, seen):
        self.response, self.seen = response, seen

    def open(self, request, timeout=None):
        self.seen.append(request)
        return self.response


class ResumeTest(unittest.TestCase):
    def download(self, folder, response):
        seen = []
        with patch.object(core.urllib.request, "build_opener",
                          return_value=FakeOpener(response, seen)):
            core._download_one("https://example.com/model.bin", Path(folder) / "model.bin",
                               threading.Event(), lambda count: None)
        return seen

    def test_part_file_is_resumed_not_restarted(self):
        # 3 GB 的模型断一次就从头下，谁都受不了。
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "model.bin.part").write_bytes(b"AAA")
            seen = self.download(tmp, FakeResponse(b"BBB", status=206))
            self.assertEqual((Path(tmp) / "model.bin").read_bytes(), b"AAABBB")
            self.assertEqual(seen[0].get_header("Range"), "bytes=3-")
            self.assertFalse((Path(tmp) / "model.bin.part").exists())

    def test_server_ignoring_range_does_not_corrupt_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "model.bin.part").write_bytes(b"AAA")
            self.download(tmp, FakeResponse(b"CCC", status=200))
            self.assertEqual((Path(tmp) / "model.bin").read_bytes(), b"CCC")

    def test_progress_is_reported_per_chunk(self):
        with tempfile.TemporaryDirectory() as tmp:
            requests, chunks = [], []
            with patch.object(core.urllib.request, "build_opener",
                              return_value=FakeOpener(FakeResponse(b"x" * 700000), requests)):
                core._download_one("https://example.com/model.bin", Path(tmp) / "model.bin",
                                   threading.Event(), chunks.append)
            self.assertEqual(sum(chunks), 700000)


class ModelscopeTest(unittest.TestCase):
    def test_url_matches_the_layout_the_installer_already_uses(self):
        self.assertEqual(core.modelscope_url("Systran/faster-whisper-tiny", "config.json"),
                         "https://modelscope.cn/models/Systran/faster-whisper-tiny/resolve/master/config.json")

    def test_missing_optional_file_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            def fake(url, target, stop, on_bytes):
                if url.endswith("vocabulary.txt"):
                    raise urllib.error.HTTPError(url, 404, "no", {}, None)
                target.write_bytes(b"x" * 8)
                on_bytes(8)

            with patch.object(core, "_download_one", side_effect=fake):
                total = core.download_from_modelscope("Systran/faster-whisper-small", tmp,
                                                      lambda *a: None, threading.Event(), 1)
            self.assertEqual(total, 8 * (len(core.MODEL_ALLOW_PATTERNS) - 1))

    def test_already_downloaded_files_are_not_fetched_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            fetched = []

            def fake(url, target, stop, on_bytes):
                fetched.append(target.name)
                target.write_bytes(b"x" * 4)

            with patch.object(core, "_download_one", side_effect=fake):
                core.download_from_modelscope("Systran/faster-whisper-small", tmp,
                                              lambda *a: None, threading.Event(), 1)
                fetched.clear()
                core.download_from_modelscope("Systran/faster-whisper-small", tmp,
                                              lambda *a: None, threading.Event(), 1)
            self.assertEqual(fetched, [])


class ConfirmDownloadTest(unittest.TestCase):
    def stub(self, chosen):
        holder = app.Application.__new__(app.Application)
        holder.asr_var = SimpleNamespace(get=lambda: chosen)
        holder.logs = []
        holder.log = holder.logs.append
        return holder

    def test_local_model_does_not_ask(self):
        holder = self.stub("small")
        with patch.object(app, "local_model_status", return_value="prepared"):
            self.assertTrue(holder.confirm_model_download())

    def test_missing_model_asks_and_says_how_big(self):
        holder = self.stub("large-v3")
        with patch.object(app, "local_model_status", return_value=""), \
             patch.object(app.messagebox, "askyesno", return_value=True) as ask:
            self.assertTrue(holder.confirm_model_download())
        self.assertIn("3090", ask.call_args[0][1])

    def test_declining_stops_before_any_download(self):
        holder = self.stub("large-v3")
        with patch.object(app, "local_model_status", return_value=""), \
             patch.object(app.messagebox, "askyesno", return_value=False):
            self.assertFalse(holder.confirm_model_download())
        self.assertTrue(any("已取消" in line for line in holder.logs))

    def test_custom_path_is_left_to_faster_whisper(self):
        holder = self.stub("D:/models/mine")
        self.assertTrue(holder.confirm_model_download())


if __name__ == "__main__":
    unittest.main()
