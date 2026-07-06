from student.core import access_control


def test_verify_guard_password_uses_guard_config(tmp_path, monkeypatch):
    guard_file = tmp_path / "student_guard.ini"
    access_control.write_guard_password("secret-123", str(guard_file))
    monkeypatch.setattr(access_control, "get_guard_config_paths", lambda: [str(guard_file)])

    assert access_control.verify_guard_password("secret-123")
    assert not access_control.verify_guard_password("wrong")


def test_verify_guard_password_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(access_control, "get_guard_config_paths", lambda: [])
    monkeypatch.delenv("LAN_CLASSROOM_INSTALL_PASSWORD", raising=False)

    assert access_control.verify_guard_password(access_control.DEFAULT_GUARD_PASSWORD)
    assert not access_control.verify_guard_password("wrong")
