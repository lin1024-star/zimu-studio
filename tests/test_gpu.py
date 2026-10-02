import hashlib
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "payload"))
import core
import gpu_runtime as gpu


class DownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = bytes(range(251)) * 5000
        cls.requests = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                DownloadTests.requests.append((self.path, self.headers.get("Range")))
                start = int(self.headers.get("Range", "bytes=0-").split("=")[1].split("-")[0])
                if self.path == "/ignore": start = 0
                body = DownloadTests.data[start:]
                self.send_response(206 if start else 200)
                self.send_header("Content-Length", str(len(body)))
                if start: self.send_header("Content-Range", f"bytes {start}-{len(DownloadTests.data)-1}/{len(DownloadTests.data)}")
                self.end_headers()
                if self.path == "/drop" and len(DownloadTests.requests) == 1:
                    body = body[:350000]
                try: self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError): pass
                self.close_connection = True
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown(); cls.server.server_close(); cls.thread.join()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.stop = threading.Event()
        self.events = []
        type(self).requests.clear()
        self.spec = {"filename": "runtime.zip", "url": f"http://127.0.0.1:{self.server.server_port}/ok",
                     "size": len(self.data), "sha256": hashlib.sha256(self.data).hexdigest()}

    def tearDown(self): self.tmp.cleanup()
    def emit(self, k, v): self.events.append((k,v))

    def test_resume_exact_and_reuse_verified_archive(self):
        (self.root / "runtime.zip.part").write_bytes(self.data[:333333])
        f = gpu.download(self.spec, self.root, self.stop, self.emit)
        self.assertEqual(f.read_bytes(), self.data)
        self.assertEqual(self.requests[0][1], "bytes=333333-")
        gpu.download(self.spec, self.root, self.stop, self.emit)
        self.assertEqual(len(self.requests), 1)

    def test_server_ignores_range_restarts_instead_of_appending(self):
        (self.root / "runtime.zip.part").write_bytes(self.data[:777])
        self.spec["url"] = self.spec["url"].replace("/ok", "/ignore")
        self.assertEqual(gpu.download(self.spec,self.root,self.stop,self.emit).read_bytes(), self.data)

    def test_disconnection_resumes_and_finishes(self):
        self.spec["url"] = self.spec["url"].replace("/ok", "/drop")
        self.assertEqual(gpu.download(self.spec,self.root,self.stop,self.emit).read_bytes(),self.data)
        self.assertGreaterEqual(len(self.requests),2)
        self.assertIsNotNone(self.requests[-1][1])

    def test_cancel_preserves_partial_not_completed(self):
        def emit(k,v):
            if k == "progress": self.stop.set()
        with self.assertRaises(core.Cancelled): gpu.download(self.spec,self.root,self.stop,emit)
        self.assertFalse((self.root/"runtime.zip").exists())
        partial=(self.root/"runtime.zip.part").stat().st_size
        self.assertGreater(partial,0); self.assertLess(partial,len(self.data))
        self.stop.clear()
        self.assertEqual(gpu.download(self.spec,self.root,self.stop,self.emit).read_bytes(),self.data)

    def test_corrupt_complete_file_is_replaced(self):
        (self.root/"runtime.zip").write_bytes(b"x"*len(self.data))
        self.assertEqual(gpu.download(self.spec,self.root,self.stop,self.emit).read_bytes(),self.data)

    def test_bad_hash_never_promoted(self):
        self.spec["sha256"]="0"*64
        with patch.object(self.stop,"wait"):
            with self.assertRaises(gpu.SetupError): gpu.download(self.spec,self.root,self.stop,self.emit)
        self.assertFalse((self.root/"runtime.zip").exists())

    def test_traversal_rejected_and_declared_files_only(self):
        archive=self.root/"test.zip";out=self.root/"out";out.mkdir()
        spec={"name":"sample","dlls":["safe.dll"]}
        with zipfile.ZipFile(archive,"w") as z:
            z.writestr("root/bin/safe.dll",b"dll");z.writestr("root/LICENSE",b"license");z.writestr("root/setup.exe",b"never extract")
        result=gpu.extract_component(archive,spec,out,self.stop)
        self.assertEqual(set(p.name for p in out.iterdir()),{"safe.dll","sample_LICENSE"})
        self.assertEqual(result["safe.dll"]["sha256"],hashlib.sha256(b"dll").hexdigest())
        with zipfile.ZipFile(archive,"w") as z: z.writestr("../outside.dll",b"bad")
        with self.assertRaises(gpu.SetupError): gpu.extract_component(archive,spec,out,self.stop)
        self.assertFalse((self.root/"outside.dll").exists())


class FallbackTests(unittest.TestCase):
    def setUp(self):
        self.stop=threading.Event();self.events=[]
        self.result=([core.Cue(1,0,2,"Hello.")],"en")

    def emit(self,k,v): self.events.append((k,v))
    def run_asr(self):return core.transcribe("sample.mp4","model","en","cuda","models",self.stop,self.emit)

    def test_missing_driver_uses_cpu(self):
        with patch.object(gpu,"prepare_gpu",side_effect=gpu.SetupError("driver absent")),patch.object(core,"_transcribe_once",return_value=self.result) as run:
            self.assertEqual(self.run_asr(),self.result)
            self.assertEqual(run.call_args.args[3],"cpu")
        self.assertIn(("device","cpu"),self.events)

    def test_gpu_oom_restarts_entire_asr_on_cpu(self):
        with patch.object(gpu,"prepare_gpu"),patch.object(core,"_transcribe_once",side_effect=[RuntimeError("CUDA out of memory"),self.result]) as run:
            self.assertEqual(self.run_asr(),self.result)
            self.assertEqual([c.args[3] for c in run.call_args_list],["cuda","cpu"])
        self.assertIn(("stage","ready"),self.events)

    def test_cancel_does_not_restart_on_cpu(self):
        with patch.object(gpu,"prepare_gpu",side_effect=core.Cancelled("stopped")),patch.object(core,"_transcribe_once") as run:
            with self.assertRaises(core.Cancelled):self.run_asr()
            run.assert_not_called()

    def test_invalid_video_does_not_retry_as_cpu(self):
        with patch.object(gpu,"prepare_gpu"),patch.object(core,"_transcribe_once",side_effect=ValueError("invalid audio")) as run:
            with self.assertRaises(core.UserError):self.run_asr()
            self.assertEqual(run.call_count,1)

    def test_runtime_permission_failure_does_not_block_cpu(self):
        with patch.object(gpu,"prepare_gpu",side_effect=PermissionError()),patch.object(core,"_transcribe_once",return_value=self.result) as run:
            self.assertEqual(self.run_asr(),self.result)
            self.assertEqual(run.call_args.args[3],"cpu")

    def test_cancel_during_gpu_asr_does_not_restart(self):
        with patch.object(gpu,"prepare_gpu"),patch.object(core,"_transcribe_once",side_effect=core.Cancelled("stop")) as run:
            with self.assertRaises(core.Cancelled):self.run_asr()
            self.assertEqual(run.call_count,1)


if __name__=="__main__":unittest.main()
