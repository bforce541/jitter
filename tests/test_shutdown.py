import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from jitter_runner.runner import ROOT, host_info
from jitter_runner.spec import load_json
from jitter_runner.storage import write_json
from test_investigation import investigation_spec


class HostProbeTests(unittest.TestCase):
    def test_optional_probe_failure_is_explicit_instead_of_crashing_setup(self):
        with patch("jitter_runner.runner.platform.system", return_value="Darwin"), patch("jitter_runner.runner.subprocess.run", side_effect=FileNotFoundError):
            host = host_info()
        self.assertEqual(host["cpu_model"], "unavailable")
        self.assertEqual(host["cpu_model_probe_error"], "FileNotFoundError")


@unittest.skipUnless(os.name == "posix" and os.environ.get("JITTER_TEST_BINARY"), "requires POSIX and compatible engine")
class ShutdownTests(unittest.TestCase):
    def test_sigterm_cleans_nested_application_group_and_preserves_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "pids"
            binary = str(Path(os.environ["JITTER_TEST_BINARY"]).resolve())
            wrapper = root / "waiting-engine"
            wrapper.write_text("#!/usr/bin/env python3\nimport os,sys,subprocess,time\nfrom pathlib import Path\n" +
                f"if sys.argv[1:] == ['--build-info']: os.execv({binary!r},[{binary!r},'--build-info'])\n" +
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])\n" +
                f"Path({str(marker)!r}).write_text(str(os.getpid())+','+str(child.pid))\n" +
                "time.sleep(60)\n")
            wrapper.chmod(0o755)
            spec = investigation_spec()["experiment"]
            write_json(root / "input.json", spec)
            output = root / "output"
            command = [sys.executable,str(ROOT / "tools/jitter.py"),"run",str(root / "input.json"),"--binary",str(wrapper),"--output",str(output)]
            driver = subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
            try:
                deadline = time.monotonic() + 15
                while not marker.exists() and driver.poll() is None and time.monotonic() < deadline:
                    time.sleep(.01)
                self.assertTrue(marker.exists(), "application must start before termination is tested")
                pids = [int(value) for value in marker.read_text().split(",")]
                driver.send_signal(signal.SIGTERM)
                stdout, stderr = driver.communicate(timeout=10)
                self.assertNotEqual(driver.returncode,0)
                self.assertIn("interrupted",stderr)
                self.assertIn("evidence retained",stderr)
                self.assertEqual(load_json(output / "manifest.json")["status"], "failed")
                for pid in pids:
                    deadline = time.monotonic() + 2
                    while True:
                        status = subprocess.run(["ps","-p",str(pid),"-o","stat="],capture_output=True,text=True,timeout=2)
                        if status.returncode or status.stdout.strip().startswith("Z"):
                            break
                        self.assertLess(time.monotonic(),deadline,f"owned test process {pid} survived termination")
                        time.sleep(.01)
            finally:
                if driver.poll() is None:
                    driver.send_signal(signal.SIGTERM)
                    try:
                        driver.communicate(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(driver.pid,signal.SIGKILL)
                        driver.communicate(timeout=2)
