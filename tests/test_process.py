import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from jitter_runner.process import run_command
from jitter_runner.spec import ExperimentError
from jitter_runner.storage import seal_bundle, verify_integrity


class ProcessTests(unittest.TestCase):
    def test_arguments_are_literal_and_capture_is_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "unexpected"
            argument = f"; touch {marker}; $(touch {marker}) `touch {marker}`"
            result = run_command([sys.executable, "-c", "import sys; print(sys.argv[1])", argument],
                                 timeout=5, capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), argument)
            self.assertEqual(result.stderr, "")
            self.assertFalse(marker.exists())
        with self.assertRaises(ValueError):
            run_command("echo bad", timeout=5)
        with self.assertRaises(ValueError):
            run_command([sys.executable], timeout=5, capture_output=True, stdout=subprocess.PIPE)

    def test_missing_compiler_is_a_launch_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                run_command([str(Path(directory) / "missing-cxx"), "--version"], timeout=5)

    def test_nonzero_status_is_preserved_and_check_raises(self):
        command = [sys.executable, "-c", "import sys; print('failed', file=sys.stderr); sys.exit(7)"]
        result = run_command(command, timeout=5, capture_output=True, text=True)
        self.assertEqual((result.returncode, result.stderr.strip()), (7, "failed"))
        with self.assertRaises(subprocess.CalledProcessError) as raised:
            run_command(command, timeout=5, capture_output=True, text=True, check=True)
        self.assertEqual(raised.exception.returncode, 7)
        self.assertEqual(raised.exception.stderr.strip(), "failed")

    @unittest.skipUnless(os.name == "posix", "process groups require POSIX")
    def test_timeout_stops_descendant_that_retains_output_pipe(self):
        script = ("import subprocess, sys, time; "
                  "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
                  "print(child.pid, flush=True); time.sleep(60)")
        started = time.monotonic()
        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            run_command([sys.executable, "-c", script], timeout=.5, capture_output=True, text=True)
        self.assertLess(time.monotonic() - started, 5)
        self.assertTrue(raised.exception.output, "parent should record its descendant before timeout")
        descendant = int(raised.exception.output.strip())
        self.assert_stopped(descendant)

    @unittest.skipUnless(os.name == "posix", "process groups require POSIX")
    def test_timeout_stops_term_ignoring_descendant_without_output_pipe(self):
        child = "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print('ready',flush=True); time.sleep(60)"
        script = ("import subprocess,sys,time; "
                  f"child=subprocess.Popen([sys.executable,'-c',{child!r}],stdout=subprocess.PIPE); "
                  "child.stdout.readline(); print(child.pid,flush=True); time.sleep(60)")
        with self.assertRaises(subprocess.TimeoutExpired) as raised:
            run_command([sys.executable,"-c",script],timeout=.5,capture_output=True,text=True)
        self.assert_stopped(int(raised.exception.output.strip()))

    @unittest.skipUnless(os.name == "posix", "process groups require POSIX")
    def test_interrupt_stops_launched_process(self):
        real_popen = subprocess.Popen
        launched = []

        def interrupting_popen(*args, **kwargs):
            process = real_popen(*args, **kwargs)
            launched.append(process)
            real_communicate = process.communicate
            calls = 0

            def communicate(*communicate_args, **communicate_kwargs):
                nonlocal calls
                calls += 1
                if calls == 1:
                    raise KeyboardInterrupt
                return real_communicate(*communicate_args, **communicate_kwargs)

            process.communicate = communicate
            return process

        with mock.patch("jitter_runner.process.subprocess.Popen", side_effect=interrupting_popen):
            with self.assertRaises(KeyboardInterrupt):
                run_command([sys.executable, "-c", "import time; time.sleep(60)"], timeout=5)
        self.assertEqual(len(launched), 1)
        self.assertIsNotNone(launched[0].poll())
        self.assert_stopped(launched[0].pid)

    def assert_stopped(self, pid):
        deadline = time.monotonic() + 2
        while True:
            status = subprocess.run(["ps", "-p", str(pid), "-o", "stat="],
                                    capture_output=True, text=True, timeout=2)
            if status.returncode or status.stdout.strip().startswith("Z"):
                return
            if time.monotonic() >= deadline:
                self.fail(f"process {pid} survived cleanup: {status.stdout.strip()}")
            time.sleep(.01)


class BundlePathTests(unittest.TestCase):
    def test_unrecorded_directory_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "contents").mkdir()
            (root / "data.txt").write_text("evidence")
            seal_bundle(root, "complete")
            verify_integrity(root, required=("data.txt",))
            (root / "alias").symlink_to(root / "contents", target_is_directory=True)
            with self.assertRaisesRegex(ExperimentError, "symlink"):
                verify_integrity(root, required=("data.txt",))


if __name__ == "__main__":
    unittest.main()
