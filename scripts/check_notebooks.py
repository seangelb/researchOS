"""Validate research notebooks and keep executed analysis offline."""
from contextlib import contextmanager
from pathlib import Path
import json
import socket

ROOT = Path(__file__).resolve().parents[1]
NOTEBOOKS = sorted((ROOT / 'vehicle' / 'notebooks').glob('*.ipynb'))


@contextmanager
def offline_guards():
    def reject(*_args, **_kwargs):
        raise RuntimeError('offline guard: network is disabled')

    original_socket = socket.socket
    socket.socket = reject
    try:
        yield
    finally:
        socket.socket = original_socket


def main():
    if not NOTEBOOKS:
        raise SystemExit('no vehicle notebooks found')
    for path in NOTEBOOKS:
        book = json.loads(path.read_text(encoding='utf-8'))
        if not book.get('cells'):
            raise SystemExit(f'{path}: missing cells')
    print(f'checked {len(NOTEBOOKS)} notebooks')


if __name__ == '__main__':
    main()
