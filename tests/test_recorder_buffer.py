import sys

import numpy as np

from meeting import config, recorder


class CountingWriter:
    def __init__(self):
        self.flushes = 0
        self.frames = 0

    def write(self, data):
        self.frames += len(data)

    def flush(self):
        self.flushes += 1

    def close(self):
        pass


class FakeStream:
    def __init__(self, callback, count):
        self.callback = callback
        self.count = count

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def start(self):
        data = np.zeros((800, 1), dtype=np.float32)
        for _ in range(self.count):
            self.callback(data, 800, None, 0)


def test_buffered_writer_flushes_once_for_many_blocks(meeting_home, monkeypatch):
    writer = CountingWriter()
    monkeypatch.setattr(recorder, "_writer", lambda path: writer)
    monkeypatch.setattr(recorder.time, "sleep", lambda s: None)
    config.update(flush_interval_sec=5.0)

    class FakeSD:
        class InputStream:
            def __init__(self, **kwargs):
                self.callback = kwargs["callback"]

            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def start(self):
                data = np.zeros((800, 1), dtype=np.float32)
                for _ in range(5):
                    self.callback(data, 800, None, 0)

        class PortAudioError(Exception):
            pass

    monkeypatch.setitem(sys.modules, "sounddevice", FakeSD)
    from meeting import session

    monkeypatch.setattr(session, "stop_requested", lambda: True)
    recorder._record_mic("job", meeting_home / "data" / "tmp" / "job.wav", None)

    assert writer.frames == 4000
    assert writer.flushes == 1  # hanya final flush, bukan per-blok


def test_flush_interval_configurable(meeting_home):
    config.update(flush_interval_sec=0.25)
    assert recorder._flush_interval() == 0.25
