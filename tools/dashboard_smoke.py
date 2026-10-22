#!/usr/bin/env python3
"""Exercise the installed dashboard command using existing local evidence."""
import argparse
import json
import queue
import signal
import subprocess
import threading
from urllib.request import urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('bundle')
    parser.add_argument('--cli', default='jitter')
    args = parser.parse_args()
    process = subprocess.Popen([args.cli, 'ui', args.bundle, '--port', '0', '--no-open'],
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines = queue.Queue()
    threading.Thread(target=lambda: [lines.put(line) for line in process.stdout], daemon=True).start()
    try:
        line = lines.get(timeout=15)
        if not line.startswith('Jitter local dashboard: http://127.0.0.1:'):
            raise RuntimeError('Dashboard did not start: ' + line)
        url = line.split(': ', 1)[1].strip()
        def get(path):
            with urlopen(url + path, timeout=30) as response:
                return response.read()
        for asset in ('/', '/style.css', '/app.js'):
            if not get(asset):
                raise RuntimeError('Missing installed dashboard asset: ' + asset)
        library = json.loads(get('/api/library'))
        identity = library['entries'][0]['id']
        detail = json.loads(get('/api/detail?id=' + identity))
        if detail['kind'] != 'investigation' or not detail['records']:
            raise RuntimeError('Missing real investigation detail')
        measurement = json.loads(get('/api/measurement?id=' + identity + '&run=' + detail['records'][0]['path']))
        if not measurement['summary']:
            raise RuntimeError('Missing measured artifact data')
        verified = json.loads(get('/api/verify?id=' + identity + '&snapshot=' + detail['snapshot']))
        if verified['status'] != 'verified':
            raise RuntimeError('Raw evidence verification failed')
        print('Installed offline dashboard command, assets, real artifact inspection and raw verification passed.')
    finally:
        process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        process.stdout.close()


if __name__ == '__main__':
    main()
