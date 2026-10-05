import imaplib
from datetime import timedelta
from types import SimpleNamespace

from rental_bot.cli import WorkerMonitor, _worker_error_category


def test_worker_monitor_alerts_once_then_sends_recovery(monkeypatch, capsys):
    sent = []
    monkeypatch.setattr("rental_bot.cli.send_telegram", lambda **kwargs: sent.append(kwargs["body"]))
    settings = SimpleNamespace(telegram_bot_token="configured", telegram_chat_id="configured")
    monitor = WorkerMonitor(settings)
    error = imaplib.IMAP4.error(b"[ALERT] Invalid credentials (Failure)")

    monitor.failed("mail_ingest", error)
    assert len(sent) == 1
    monitor.failed("mail_ingest", error)
    assert len(sent) == 1
    assert "mail_auth_rejected" in sent[0]
    assert "Invalid credentials" not in capsys.readouterr().out

    monitor.recovered()
    assert len(sent) == 2
    assert "recovered" in sent[1]
    assert monitor.failure_streak == 0


def test_worker_monitor_reminds_after_one_day(monkeypatch):
    sent = []
    monkeypatch.setattr("rental_bot.cli.send_telegram", lambda **kwargs: sent.append(kwargs["body"]))
    settings = SimpleNamespace(telegram_bot_token="configured", telegram_chat_id="configured")
    monitor = WorkerMonitor(settings)

    monitor.failed("mail_ingest", OSError("network unavailable"))
    monitor.failed("mail_ingest", OSError("network unavailable"))
    monitor.last_alert_at -= timedelta(hours=25)
    monitor.failed("mail_ingest", OSError("network unavailable"))

    assert len(sent) == 2
    assert "failed cycles: 3" in sent[1]


def test_worker_monitor_alert_delivery_failure_does_not_crash(monkeypatch, capsys):
    def fail(**kwargs):
        raise RuntimeError("token-sensitive-details")

    monkeypatch.setattr("rental_bot.cli.send_telegram", fail)
    settings = SimpleNamespace(telegram_bot_token="configured", telegram_chat_id="configured")
    monitor = WorkerMonitor(settings)

    monitor.failed("notification", RuntimeError("private-listing-details"))
    monitor.failed("notification", RuntimeError("private-listing-details"))

    output = capsys.readouterr().out
    assert "token-sensitive-details" not in output
    assert "private-listing-details" not in output
    assert "delivery failed" in output
    assert not monitor.alerted


def test_worker_error_classification_redacts_mail_response():
    assert _worker_error_category(imaplib.IMAP4.error(b"[ALERT] Invalid credentials (Failure)")) == "mail_auth_rejected"
    assert _worker_error_category(imaplib.IMAP4.error(b"private server text")) == "mail_protocol"
