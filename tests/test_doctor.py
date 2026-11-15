import pytest

pytest.importorskip("PySide6")

from camchord import doctor  # noqa: E402


def test_doctor_runs_and_reports(capsys, monkeypatch):
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    code = doctor.run()
    out = capsys.readouterr().out
    assert out.startswith("CamChord ")
    assert "PySide6" in out and "ffmpeg" in out
    assert code in (0, 1) and ("ready" in out)
