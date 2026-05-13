"""Unit tests for load_config — pure parsing, no PowerShell needed."""
from pathlib import Path

import server


def _patch_script(tmp_path, monkeypatch, contents: str | None):
    """Point server.LOGIN_ALERT_SCRIPT at a temp file (or non-existent path)."""
    p = tmp_path / "LoginAlert.ps1"
    if contents is not None:
        p.write_text(contents, encoding="utf-8")
    monkeypatch.setattr(server, "LOGIN_ALERT_SCRIPT", p)
    return p


def test_load_config_missing_script(tmp_path, monkeypatch):
    _patch_script(tmp_path, monkeypatch, None)
    cfg = server.load_config()
    assert "error" in cfg
    assert "LoginAlert.ps1" in cfg["error"]


def test_load_config_extracts_both_values(tmp_path, monkeypatch):
    _patch_script(tmp_path, monkeypatch, """
$PhoneIP   = "192.168.1.50"
$NtfyTopic = "pc-alert-abcd1234"
$LogFile   = "C:\\Scripts\\login.log"
""")
    cfg = server.load_config()
    assert cfg == {"phone_ip": "192.168.1.50", "ntfy_topic": "pc-alert-abcd1234"}


def test_load_config_handles_missing_phone_ip(tmp_path, monkeypatch):
    _patch_script(tmp_path, monkeypatch, '$NtfyTopic = "topic-only"\n')
    cfg = server.load_config()
    assert cfg["phone_ip"] is None
    assert cfg["ntfy_topic"] == "topic-only"


def test_load_config_handles_missing_topic(tmp_path, monkeypatch):
    _patch_script(tmp_path, monkeypatch, '$PhoneIP = "10.0.0.5"\n')
    cfg = server.load_config()
    assert cfg["phone_ip"] == "10.0.0.5"
    assert cfg["ntfy_topic"] is None


def test_load_config_ignores_surrounding_whitespace_and_comments(tmp_path, monkeypatch):
    _patch_script(tmp_path, monkeypatch, """
# leading comment
   $PhoneIP   =   "192.168.1.99"   # trailing comment
$NtfyTopic= "very-long-topic-name-with-dashes"
""")
    cfg = server.load_config()
    # Current regex requires the exact `=` placement; document the contract.
    assert cfg["phone_ip"] == "192.168.1.99"
    assert cfg["ntfy_topic"] == "very-long-topic-name-with-dashes"
