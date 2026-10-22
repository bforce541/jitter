"""Local artifact reader and HTTP boundaries, including genuine measured evidence."""
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from jitter_runner.dashboard import Library, confined, make_server, MAX_TEXT
from jitter_runner.runner import run_experiment
from jitter_runner.spec import ExperimentError, load_spec

ROOT = Path(__file__).resolve().parents[1]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


class DashboardTests(unittest.TestCase):
    def test_partial_failed_and_malformed_results_are_honest_and_refreshable(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root/'live/investigation-spec.json', {'experiment':{'application':{'adapter':'json-projection-v1'}}})
            write(root/'live/state.json', {'status':'running', 'records':{'measurements/run-00001':{'status':'failed', 'error':'oracle failed'}}})
            write(root/'broken/investigation-spec.json', {})
            (root/'broken/report.json').write_text('{')
            library = Library(root)
            data = library.discover()
            self.assertEqual(len(data['entries']), 1)
            self.assertEqual(data['entries'][0]['status'], 'running')
            self.assertEqual(len(data['warnings']), 1)
            identity = data['entries'][0]['id']
            detail = library.detail(identity)
            self.assertEqual(detail['records'][0]['status'], 'failed')
            self.assertEqual(detail['report'], {})
            write(root/'live/state.json', {'status':'running', 'builds':{'narrow':{'status':'failed'}}, 'records':{}})
            (root/'live/engines/narrow/logs').mkdir(parents=True)
            (root/'live/engines/narrow/logs/build.stderr.log').write_text('compile failure')
            self.assertEqual(library.detail(identity)['build_logs']['narrow']['build.stderr.log']['text'], 'compile failure')
            snapshot = library.detail(identity)['snapshot']
            write(root/'live/report.json', {'decision':'invalid_investigation'})
            with self.assertRaisesRegex(ExperimentError, 'changed'):
                library.verify(identity, snapshot)
            self.assertEqual(library.detail(identity)['report']['decision'], 'invalid_investigation')
            with self.assertRaises(ExperimentError):
                library.measurement(identity, '../../personal')

    def test_links_traversal_nonfinite_and_oversized_artifacts_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write(root/'outside.json', {'secret':'must not read'})
            (root/'bundle').mkdir()
            (root/'bundle/investigation-spec.json').symlink_to(root/'outside.json')
            library = Library(root)
            self.assertFalse(library.discover()['entries'])
            self.assertIn('Symbolic links', library.discover()['warnings'][0])
            for relative in ('../outside.json', '/etc/passwd', 'bundle/investigation-spec.json'):
                with self.assertRaises(ExperimentError):
                    confined(root, relative)
            (root/'bundle/investigation-spec.json').unlink()
            (root/'bundle/investigation-spec.json').write_text('{"bad":NaN}')
            self.assertFalse(library.discover()['entries'])
            (root/'bundle/investigation-spec.json').write_text('[]')
            self.assertIn('JSON object', library.discover()['warnings'][0])
            with patch('jitter_runner.dashboard.MAX_JSON', 2):
                self.assertFalse(library.discover()['entries'])

    def test_http_is_loopback_only_same_origin_and_has_no_execution_or_file_endpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch('socket.getfqdn', side_effect=AssertionError('local startup must not resolve DNS')):
                server = make_server(directory)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
                connection.request('GET', '/')
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                self.assertIn("default-src 'self'", response.getheader('Content-Security-Policy'))
                self.assertIn(b'app.js', response.read())
                for path, headers, code in [('/api/library', {}, 200), ('/api/library', {'Host':'attacker.test'},403),
                        ('/api/library', {'Origin':'https://attacker.test'},403), ('/api/library', {'Sec-Fetch-Site':'cross-site'},403), ('/api/detail?id=unknown',{},400),
                        ('/../../pyproject.toml',{},404), ('/api/run',{},404), ('/api/measurement?id=unknown&run=../x',{},400)]:
                    connection.request('GET',path,headers=headers)
                    response = connection.getresponse()
                    self.assertEqual(response.status,code,(path,headers))
                    response.read()
                connection.request('POST','/api/run',body=b'{}')
                response=connection.getresponse()
                self.assertEqual(response.status,501)
                response.read()
                connection.close()
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_real_measurement_is_read_verified_and_tampering_is_detected(self):
        executable = os.environ.get('JITTER_TEST_BINARY')
        if not executable:
            self.skipTest('JITTER_TEST_BINARY is needed for real measurement coverage')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = load_spec(ROOT/'examples/baseline.json')
            spec['repetitions']=1
            spec['workload'].update(warmup_ms=1, measurement_ms=10)
            bundle, original = run_experiment(spec, root/'actual', binary=Path(executable))
            library=Library(root)
            identity=library.discover()['entries'][0]['id']
            self.assertEqual(library.measurement(identity,'.')['summary'],original)
            self.assertEqual(library.verify(identity)['status'],'verified')
            (bundle/'run-001/stderr.log').write_text('changed evidence')
            with self.assertRaises(ExperimentError):
                library.verify(identity)
            log=library.measurement(identity,'.')['runs'][0]['logs']['stderr.log']
            self.assertEqual(log['text'],'changed evidence')
            (bundle/'run-001/stderr.log').write_text('x'*(MAX_TEXT+100))
            self.assertTrue(library.measurement(identity,'.')['runs'][0]['logs']['stderr.log']['truncated'])

    def test_verification_serializes_expensive_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            write(root/'spec.json',{})
            write(root/'summary.json',{})
            library=Library(root)
            identity=library.discover()['entries'][0]['id']
            library.verify_lock.acquire()
            try:
                with self.assertRaisesRegex(ExperimentError,'already running'):
                    library.verify(identity)
            finally:
                library.verify_lock.release()


if __name__ == '__main__':
    unittest.main()
