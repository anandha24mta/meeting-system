import pytest

from meeting import button


def test_initial_idle():
    st = button.ButtonState()
    assert st.phase == "idle"


def test_start_then_recording():
    st = button.ButtonState()
    st.start(source="mic", job_id="j1")
    assert st.phase == "recording"


def test_stop_returns_elapsed_and_idle(monkeypatch):
    st = button.ButtonState()
    st.start(source="mic", job_id="j1")
    monkeypatch.setattr(button.time, "monotonic", lambda: st.t0 + 65.0)
    assert st.stop() == pytest.approx(65.0)
    assert st.phase == "idle"


def test_reuse_after_stop(monkeypatch):
    st = button.ButtonState()
    st.start(source="mic", job_id="a")
    monkeypatch.setattr(button.time, "monotonic", lambda: st.t0 + 5)
    st.stop()
    st.start(source="sys", job_id="b")
    assert st.phase == "recording"
    monkeypatch.setattr(button.time, "monotonic", lambda: st.t0 + 2)
    assert st.stop() == pytest.approx(2.0)


def test_source_and_job_id_roundtrip():
    st = button.ButtonState()
    st.start(source="both", job_id="xyz")
    assert st.source == "both"
    assert st.job_id == "xyz"
