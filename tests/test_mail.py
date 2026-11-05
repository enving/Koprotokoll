"""Versand gegen einen lokalen SMTP-Server sowie Passwort- und TLS-Fehler."""

import secrets
import smtplib
import socketserver
import threading
from email import policy
from email.parser import BytesParser

import pytest

from koprotokoll.mail import MailError, SmtpMailSender
from test_integrations import make_client


@pytest.fixture
def smtp_server():
    messages = []

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            self.wfile.write(b"220 localhost test\r\n")
            recipients = []
            while line := self.rfile.readline():
                command = line.decode().strip()
                if command.upper().startswith(("EHLO", "HELO")):
                    self.wfile.write(b"250 localhost\r\n")
                elif command.upper().startswith("RCPT TO:"):
                    recipients.append(command)
                    self.wfile.write(b"250 OK\r\n")
                elif command.upper() == "DATA":
                    self.wfile.write(b"354 Eingabe\r\n")
                    body = []
                    while (part := self.rfile.readline()) != b".\r\n":
                        if not part:
                            return
                        body.append(part.removeprefix(b".") if part.startswith(b"..") else part)
                    messages.append((list(recipients), BytesParser(policy=policy.default).parsebytes(b"".join(body))))
                    self.wfile.write(b"250 Gespeichert\r\n")
                elif command.upper() == "QUIT":
                    self.wfile.write(b"221 Ende\r\n")
                    break
                else:
                    self.wfile.write(b"250 OK\r\n")

    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1], messages
    server.shutdown()
    server.server_close()
    thread.join()


# Zufällige Testwerte statt fester Zeichenketten: so tauchen keine Passwörter im Quelltext auf.
API_PASSWORD = secrets.token_urlsafe(12)
AUTH_PASSWORD = secrets.token_urlsafe(12)
INPUT_PASSWORD = secrets.token_urlsafe(12)


def config(port=25, **values):
    return {"host": "127.0.0.1", "port": port, "tls": "none", "user": "", "password": "",
            "sender": "ich@example.com", **values}


def test_smtp_message_has_german_subject_and_html(smtp_server):
    port, messages = smtp_server
    SmtpMailSender(config(port)).send(["ben@example.com"], "Grüße zur Besprechung", "Grüße", "<p>Grüße</p>")
    assert len(messages) == 1
    recipients, msg = messages[0]
    assert recipients == ["rcpt TO:<ben@example.com>"]
    assert msg["Subject"] == "Grüße zur Besprechung"
    assert msg["From"] == "ich@example.com"
    assert msg.get_body(preferencelist=("plain",)).get_content().strip() == "Grüße"
    assert msg.get_body(preferencelist=("html",)).get_content().strip() == "<p>Grüße</p>"


def test_smtp_api_persistence_password_mask_and_recap(tmp_path, smtp_server):
    port, messages = smtp_server
    with make_client(tmp_path) as client:
        api_config = {**config(port), "provider": "smtp", "password": API_PASSWORD, "send_recap": True,
                      "recap_to_attendees": True}
        r = client.put("/api/integrations/email", json=api_config)
        assert r.status_code == 200
        assert r.json()["smtp"]["password_set"]
        assert API_PASSWORD not in r.text and "password" not in r.json()["smtp"]
        assert client.post("/api/integrations/email/test").status_code == 200
        client.app.state.events.emit("meeting.completed", {
            "id": 3, "title": "Überblick", "duration": 60, "summary": {},
            "meta": {"source": "bot", "attendees": ["ich@example.com", "ben@example.com", "ben@example.com"]},
        })
        client.app.state.events.join()
        assert len(messages) == 2
        assert messages[1][1]["To"] == "ich@example.com, ben@example.com"
    with make_client(tmp_path) as client:
        r = client.get("/api/integrations/email")
        assert r.json()["provider"] == "smtp" and r.json()["smtp"]["password_set"]
        assert API_PASSWORD not in r.text
        client.put("/api/integrations/email", json={"password": ""})
        assert not client.get("/api/integrations/email").json()["smtp"]["password_set"]


@pytest.mark.parametrize("tls", ["ssl", "starttls"])
def test_tls_and_authentication_errors_are_redacted(monkeypatch, tls, caplog):
    calls = []

    class SMTP:
        def __init__(self, *args, **kwargs):
            if tls == "ssl":
                assert kwargs["context"].check_hostname
            calls.append("connect")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def starttls(self, *, context):
            assert context.check_hostname
            calls.append("tls")

        def login(self, user, password):
            calls.append("login")
            raise smtplib.SMTPAuthenticationError(535, AUTH_PASSWORD.encode())

    monkeypatch.setattr(smtplib, "SMTP_SSL" if tls == "ssl" else "SMTP", SMTP)
    with pytest.raises(MailError) as error:
        SmtpMailSender(config(tls=tls, user="test", password=AUTH_PASSWORD)).send(
            ["ich@example.com"], "Test", "Text", "<p>Text</p>")
    assert AUTH_PASSWORD not in str(error.value) + caplog.text
    assert calls == (["connect", "tls", "login"] if tls == "starttls" else ["connect", "login"])


def test_email_api_requires_key_and_rejects_insecure_smtp(tmp_path):
    with make_client(tmp_path) as client:
        assert client.get("/api/integrations/email", headers={"X-Forwarded-For": "198.51.100.1"}).status_code == 401
        assert client.put("/api/integrations/email", json={"provider": "smtp", **config(host="mail.example.com")}).status_code == 400
        assert client.post("/api/integrations/email/test").status_code == 400


def test_provider_change_preserves_separate_recap_preferences(tmp_path):
    with make_client(tmp_path) as client:
        client.patch("/api/integrations/google/settings", json={"send_recap": True, "recap_to_attendees": True})
        r = client.put("/api/integrations/email", json={"provider": "smtp", **config()}).json()
        assert not r["send_recap"] and not r["recap_to_attendees"]
        client.put("/api/integrations/email", json={"send_recap": True})
        client.put("/api/integrations/email", json={"provider": "gmail", "send_recap": False})
        r = client.put("/api/integrations/email", json={"provider": "smtp"}).json()
        assert r["send_recap"] and not r["recap_to_attendees"]


def test_validation_error_never_echoes_password_input(tmp_path):
    with make_client(tmp_path) as client:
        for body in ({"password": {"sensitive": INPUT_PASSWORD}},
                     {"password": INPUT_PASSWORD, "port": "kein-port"}):
            response = client.put("/api/integrations/email", json=body)
            assert response.status_code == 422
            assert INPUT_PASSWORD not in response.text
            assert all("input" not in item for item in response.json()["detail"])
