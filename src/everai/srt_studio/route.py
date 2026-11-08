"""Coordinate proxy rotation across parallel cue engines without serializing TTS."""
from contextlib import contextmanager
import threading

try:
    from ..client import EverAICancelled
except ImportError:
    from client import EverAICancelled


class RouteCoordinator:
    """Concurrent request leases; an IP change drains leases and blocks new ones.

    A thread asking for rotation relinquishes its own lease first, avoiding
    deadlock when several requests fail together. Epochs coalesce their rotations.
    Each core engine retains its existing retry, CSRF/profile and proxy behavior.
    """
    def __init__(self):
        self._condition = threading.Condition()
        self._local = threading.local()
        self._active = 0
        self._waiting = 0
        self._rotating = False
        self._epoch = 0

    @staticmethod
    def _check(cancel):
        if cancel.is_set():
            raise EverAICancelled('Đã dừng tác vụ SRT.')

    def _enter(self, lease, cancel):
        with self._condition:
            while self._rotating or self._waiting:
                self._check(cancel)
                self._condition.wait(.05)
            self._check(cancel)
            lease.update(active=True, epoch=self._epoch)
            self._active += 1

    def _leave(self, lease):
        with self._condition:
            if lease['active']:
                lease['active'] = False
                self._active -= 1
                self._condition.notify_all()

    @contextmanager
    def request(self, cancel):
        if getattr(self._local, 'lease', None) is not None:
            raise RuntimeError('Nested SRT route lease')
        lease = dict(active=False, epoch=0)
        self._enter(lease, cancel)
        self._local.lease = lease
        try:
            yield
        finally:
            self._leave(lease)
            self._local.lease = None

    def rotate(self, operation, current_route, cancel, force_rotate=False):
        lease = getattr(self._local, 'lease', None)
        with self._condition:
            epoch = lease['epoch'] if lease else self._epoch
            if lease:
                self._leave(lease)
            self._waiting += 1
        owns_rotation = False
        try:
            try:
                with self._condition:
                    while self._rotating or (self._epoch == epoch and self._active):
                        self._check(cancel)
                        self._condition.wait(.05)
                    self._check(cancel)
                    owns_rotation = self._epoch == epoch
                    if owns_rotation:
                        self._rotating = True
            finally:
                with self._condition:
                    self._waiting -= 1
                    self._condition.notify_all()
            if owns_rotation:
                return operation(force_rotate=force_rotate)
            return current_route()
        finally:
            if owns_rotation:
                with self._condition:
                    self._epoch += 1
                    self._rotating = False
                    self._condition.notify_all()
            if lease and not cancel.is_set():
                self._enter(lease, cancel)

    def bind(self, engine):
        """Wrap this fresh child instance only; normal text/TXT engines are untouched."""
        engine.prefer_csrf_head = True
        original_render = engine._process_single_chunk
        original_rotate = engine.rotate_proxy
        def render(*args, **kwargs):
            with self.request(engine.cancel_event):
                return original_render(*args, **kwargs)
        def rotate(force_rotate=False):
            return self.rotate(original_rotate, engine.get_current_proxy,
                               engine.cancel_event, force_rotate)
        engine._process_single_chunk = render
        engine.rotate_proxy = rotate
