"""Read-only, loopback-only artifact inspection. No benchmark execution endpoints."""

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
from socketserver import TCPServer
import threading
from urllib.parse import parse_qs, urlsplit
import webbrowser

from .spec import ExperimentError

ASSETS = Path(__file__).with_name('ui_assets')
MAX_JSON = 16 * 1024 * 1024
MAX_TEXT = 128 * 1024
PRUNE = {'sources', 'driver-sources', 'engines', 'measurements', 'build', 'build-logs',
         'third_party', 'node_modules', '__pycache__', 'release-fresh-checkout', 'release-fresh-env'}


def read_json(path, default=None):
    if not path.is_file():
        return default
    if path.stat().st_size > MAX_JSON:
        raise ExperimentError('Artifact exceeds dashboard JSON size limit')
    with path.open(encoding='utf-8') as stream:
        value = json.load(stream, parse_constant=lambda token: (_ for _ in ()).throw(ValueError('Non-finite JSON value')))
    if not isinstance(value, dict):
        raise ExperimentError('Expected a JSON object artifact')
    return value


def confined(root, relative):
    """Reject links, even when a link currently points inside the permitted root."""
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ExperimentError('Artifact path is outside the selected bundle')
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ExperimentError('Symbolic links are not dashboard artifacts')
    if not current.resolve().is_relative_to(root.resolve()):
        raise ExperimentError('Artifact path is outside the selected bundle')
    return current


# Path.is_relative_to is available in the minimum supported Python 3.9.
class Library:
    def __init__(self, root):
        self.root = Path(root).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ExperimentError('Dashboard root must be a result directory')
        self.paths = {}
        self.lock = threading.RLock()
        self.verify_lock = threading.Lock()

    def discover(self):
        entries, warnings, paths = [], [], {}
        visited = 0
        for directory, children, files in os.walk(self.root, followlinks=False):
            base = Path(directory)
            visited += 1
            if visited > 30000 or len(entries) >= 2000:
                warnings.append('Discovery limit reached. Select a narrower result directory.')
                break
            children[:] = sorted(name for name in children if not name.startswith('.')
                                 and name not in PRUNE and not name.startswith('build-')
                                 and not (base / name).is_symlink())
            if len(base.relative_to(self.root).parts) >= 9:
                children[:] = []
            kind = ('investigation' if 'investigation-spec.json' in files else
                    'overhead' if 'overhead-spec.json' in files else
                    'measurement' if 'spec.json' in files and 'summary.json' in files else None)
            if kind is None:
                continue
            children[:] = []
            relative = str(base.relative_to(self.root))
            identity = hashlib.sha256(relative.encode()).hexdigest()[:20]
            try:
                confined(self.root, relative)
                spec = read_json(confined(base, {'investigation':'investigation-spec.json', 'overhead':'overhead-spec.json', 'measurement':'spec.json'}[kind]), {})
                report = read_json(confined(base, 'report.json' if kind == 'investigation' else 'comparison.json' if kind == 'overhead' else 'summary.json'), {})
                state = read_json(confined(base, 'state.json'), {}) if kind == 'investigation' else {}
                experiment = spec.get('experiment', spec)
                entries.append({'id': identity, 'path': relative, 'kind': kind,
                                'application': experiment.get('application', {}).get('adapter', 'shared-index-v1'),
                                'status': report.get('decision', report.get('status', state.get('status', 'incomplete'))),
                                'strategy': spec.get('search', {}).get('strategy'),
                                'runs': report.get('cost', {}).get('runs'),
                                'created': state.get('created_at_utc'), 'sealed': (base / 'manifest.json').is_file()})
                paths[identity] = (base, kind)
            except (OSError, ValueError, TypeError, AttributeError, ExperimentError) as error:
                warnings.append(f'{relative}: {error}')
        with self.lock:
            self.paths = paths
        return {'root': str(self.root), 'entries': entries, 'warnings': warnings,
                'interpretation': 'Stored artifact decisions. Verification is separate and reruns raw evidence checks.'}

    def lookup(self, identity):
        with self.lock:
            if identity not in self.paths:
                raise ExperimentError('Unknown result. Refresh the library.')
            path, kind = self.paths[identity]
        confined(self.root, path.relative_to(self.root))
        return path, kind

    def detail(self, identity):
        path, kind = self.lookup(identity)
        names = {'investigation': ('investigation-spec.json', 'report.json', 'state.json'),
                 'overhead': ('overhead-spec.json', 'comparison.json', 'execution.json'),
                 'measurement': ('spec.json', 'summary.json', 'metadata.json')}[kind]
        before = self.snapshot(path, names)
        spec, report, state = [read_json(confined(path, name), {}) for name in names]
        if before != self.snapshot(path, names):
            raise ExperimentError('Result changed while reading; refresh to retry')
        records = []
        for name, row in state.get('records', {}).items():
            records.append({'path': name, **row})
        if kind == 'measurement':
            records = [{'path': '.', 'status': report.get('status', 'incomplete'), 'stage': 'measurement'}]
        if kind == 'overhead':
            records = [{'path': row['bundle'], 'stage': 'tracing ' + row['mode'], 'status':'stored'} for row in state.get('order', [])]
        build_logs = {}
        for implementation in state.get('builds', {}):
            logs = confined(path, Path('engines') / implementation / 'logs')
            build_logs[implementation] = {}
            if logs.is_dir():
                for file in sorted(logs.glob('*.log'))[:10]:
                    confined(path, file.relative_to(path))
                    with file.open('rb') as stream:
                        content = stream.read(MAX_TEXT + 1)
                    build_logs[implementation][file.name] = {'text': content[:MAX_TEXT].decode('utf-8', errors='replace'), 'truncated': len(content) > MAX_TEXT}
        return {'id': identity, 'kind': kind, 'path': str(path), 'spec': spec, 'report': report, 'build_logs': build_logs,
                'state': state, 'records': records, 'snapshot': before,
                'reproduce': 'jitter reproduce ' + shlex.quote(str(path)) + ' --output ' + shlex.quote(str(path.with_name(path.name + '-replay'))),
                'verify': 'jitter verify ' + shlex.quote(str(path))}

    @staticmethod
    def snapshot(path, names):
        digest = hashlib.sha256()
        for name in names:
            file = confined(path, name)
            if file.is_file():
                if file.stat().st_size > MAX_JSON:
                    raise ExperimentError('Artifact exceeds dashboard JSON size limit')
                digest.update(name.encode())
                digest.update(file.read_bytes())
        return digest.hexdigest()

    def measurement(self, identity, name, repetition=1):
        detail = self.detail(identity)
        if name not in {row['path'] for row in detail['records']}:
            raise ExperimentError('Measurement is not registered in this result')
        base = confined(Path(detail['path']), name)
        data = {key: read_json(confined(base, filename), {}) for key, filename in
                [('spec','spec.json'), ('summary','summary.json'), ('environment','metadata.json')]}
        data['runs'] = []
        directories = sorted(item for item in base.glob('run-*') if item.is_dir())[:100]
        data['available_runs'] = [item.name for item in directories]
        if not 1 <= repetition <= max(1, len(directories)):
            raise ExperimentError('Repetition is outside the stored measurement')
        for directory in directories[repetition-1:repetition]:
            if not directory.is_dir():
                continue
            confined(base, directory.relative_to(base))
            run = {'name': directory.name}
            for key, filename in [('summary','summary.json'), ('diagnostics','diagnostics.json'), ('execution','execution.json')]:
                run[key] = read_json(confined(base, directory.relative_to(base) / filename), {})
            run['logs'] = {}
            for filename in ('stdout.log', 'stderr.log'):
                file = confined(base, directory.relative_to(base) / filename)
                if file.is_file():
                    with file.open('rb') as stream:
                        content = stream.read(MAX_TEXT + 1)
                    run['logs'][filename] = {'text': content[:MAX_TEXT].decode('utf-8', errors='replace'), 'truncated': len(content) > MAX_TEXT}
            data['runs'].append(run)
        return data

    def verify(self, identity, snapshot=None):
        path, kind = self.lookup(identity)
        if snapshot is not None and self.detail(identity)['snapshot'] != snapshot:
            raise ExperimentError('Displayed result has changed; refresh before verifying')
        if not self.verify_lock.acquire(blocking=False):
            raise ExperimentError('Verification already running. Try again after it finishes.')
        try:
            # Avoid invoking validators on symbolic links nested in a selected bundle.
            # The server is a local viewer; the CLI remains the authoritative verifier.
            for directory, children, files in os.walk(path, followlinks=False):
                for name in children + files:
                    if (Path(directory) / name).is_symlink():
                        raise ExperimentError('Verification refuses a bundle containing symbolic links')
            if kind == 'investigation':
                from .investigation import verify_investigation
                verify_investigation(path)
            elif kind == 'overhead':
                from .overhead import verify_overhead
                verify_overhead(path)
            else:
                from .runner import verify_bundle
                verify_bundle(path)
            if snapshot is not None and self.detail(identity)['snapshot'] != snapshot:
                raise ExperimentError('Displayed result changed during verification; refresh and retry')
            return {'status': 'verified', 'scope': 'Raw evidence and semantic checks passed at this instant. Files may change afterward; reverify before relying on a decision.'}
        finally:
            self.verify_lock.release()


def make_server(root, port=0):
    library = Library(root)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, code, body, mime='application/json; charset=utf-8'):
            if not isinstance(body, bytes):
                body = json.dumps(body, allow_nan=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            host = f'127.0.0.1:{self.server.server_port}'
            parsed = urlsplit(self.path)
            foreign_api = (parsed.path.startswith('/api/') and
                           (self.headers.get('Origin', 'http://' + host) != 'http://' + host or
                            self.headers.get('Sec-Fetch-Site') == 'cross-site'))
            if self.headers.get('Host') != host or foreign_api:
                return self.send(403, {'error': 'Only the local dashboard origin may access artifacts'})
            query = parse_qs(parsed.query)
            try:
                if parsed.path in ('/', '/app.js', '/style.css'):
                    name = {'/':'index.html','/app.js':'app.js','/style.css':'style.css'}[parsed.path]
                    mime = {'/':'text/html; charset=utf-8','/app.js':'text/javascript; charset=utf-8','/style.css':'text/css; charset=utf-8'}[parsed.path]
                    return self.send(200, (ASSETS / name).read_bytes(), mime)
                if parsed.path == '/api/library':
                    return self.send(200, library.discover())
                identity = query.get('id', [''])[0]
                if parsed.path == '/api/detail':
                    return self.send(200, library.detail(identity))
                if parsed.path == '/api/measurement':
                    return self.send(200, library.measurement(identity, query.get('run', [''])[0], int(query.get('repetition', ['1'])[0])))
                if parsed.path == '/api/verify':
                    return self.send(200, library.verify(identity, query.get('snapshot', [None])[0]))
                self.send(404, {'error':'Unknown dashboard endpoint'})
            except (OSError, ValueError, KeyError, TypeError, AttributeError, ExperimentError) as error:
                self.send(400, {'error': str(error)})

    class LoopbackServer(ThreadingHTTPServer):
        def server_bind(self):
            # The numeric local bind needs no reverse DNS. HTTPServer's default
            # getfqdn() can stall startup on hosts with slow resolver policy.
            TCPServer.server_bind(self)
            self.server_name = '127.0.0.1'
            self.server_port = self.server_address[1]

    server = LoopbackServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.library = library
    return server


def serve(root, port=8765, open_browser=True):
    if not 0 <= port <= 65535:
        raise ExperimentError('Port must be between 0 and 65535')
    server = make_server(root, port)
    url = f'http://127.0.0.1:{server.server_port}'
    print(f'Jitter local dashboard: {url}\nArtifacts: {server.library.root}\nRead-only; Ctrl-C to stop.', flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
