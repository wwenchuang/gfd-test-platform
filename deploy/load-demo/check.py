#!/usr/bin/env python3
"""Local low-impact checks only. Does not run stress, CPU, memory or failures."""
import time
import urllib.error
import urllib.request
from pathlib import Path


def open_when_ready(request, attempts=20, delay_seconds=1):
    """Wait for the container port to become ready; never hide HTTP failures."""
    for attempt in range(1, attempts + 1):
        try:
            return urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError:
            raise
        except (ConnectionError, TimeoutError, urllib.error.URLError):
            if attempt == attempts:
                raise
            time.sleep(delay_seconds)


def main():
    root = Path(__file__).resolve().parent
    for path, key in [('/health', None), ('/demo/ok', 'api'), ('/metrics', 'metrics')]:
        headers = {}
        if key:
            headers['Authorization'] = 'Bearer ' + (root/'secrets'/(key+'_token')).read_text().strip()
        request = urllib.request.Request('http://127.0.0.1:18080' + path, headers=headers)
        with open_when_ready(request) as response:
            data = response.read().decode()
            print(path, response.status)
            if path == '/metrics':
                print(data)
    print('仅证明本机服务与自身cgroup指标可读，不等于平台/专用节点可达。')


if __name__ == '__main__':
    main()
