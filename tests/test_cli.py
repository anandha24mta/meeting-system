# tests/test_cli.py — bagian devices (file dilengkapi di Task 12)
import json

from meeting import devices


class FakeSD:
    @staticmethod
    def query_devices():
        return [
            {"name": "Laptop Mic", "max_input_channels": 2, "max_output_channels": 0, "index": 0},
            {"name": "Speakers", "max_input_channels": 0, "max_output_channels": 2, "index": 1},
            {"name": "Jabra Headset", "max_input_channels": 1, "max_output_channels": 1, "index": 2},
        ]


class FakeLoopMic:
    def __init__(self, name, isloopback):
        self.name = name
        self.isloopback = isloopback


class FakeSC:
    @staticmethod
    def all_microphones(include_loopback=False):
        assert include_loopback is True
        return [
            FakeLoopMic("Laptop Mic", False),
            FakeLoopMic("Jabra Headset", False),
            FakeLoopMic("Speaker (Realtek) Loopback", True),
        ]


def test_list_devices_mics_and_loopback(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "sounddevice", FakeSD)
    monkeypatch.setitem(sys.modules, "soundcard", FakeSC)
    result = devices.list_devices()
    names = [(d["kind"], d["name"]) for d in result]
    assert ("mic", "Laptop Mic") in names
    assert ("mic", "Jabra Headset") in names
    assert ("loopback", "Speaker (Realtek) Loopback") in names
    assert ("mic", "Speakers") not in names  # output-only excluded
    assert ("loopback", "Laptop Mic") not in names


def test_set_mic_persists_config(monkeypatch, meeting_home):
    devices.set_mic("Jabra Headset")
    from meeting import config
    assert config.load()["mic_device"] == "Jabra Headset"
    devices.set_mic(None)
    assert config.load()["mic_device"] is None
