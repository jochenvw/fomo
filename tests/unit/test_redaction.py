from __future__ import annotations

import json
import logging

from contoso_workshop.telemetry.logging import JsonLineFormatter, redact, redact_obj, write_trace

JWT = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl"


def test_redact_text_patterns():
    text = (f"Authorization: Bearer {JWT} url=https://a.blob.core.windows.net/c/b?sv=2024&sig=AbC%2Bd&se=1 "
            'api-key: 12345 {"password": "hunter2"} AccountKey=Zm9vYmFy;EndpointSuffix=x')
    out = redact(text)
    for secret in (JWT, "AbC%2Bd", "12345", "hunter2", "Zm9vYmFy"):
        assert secret not in out
    assert "sv=2024" in out and "se=1" in out and "EndpointSuffix=x" in out
    assert redact(f"token {JWT}") == "token ***jwt***"


def test_redact_obj_by_key_and_nested():
    obj = {"api_key": "k", "access_token": "t", "SAS": "s", "client_credential": "c", "name": "ok",
           "nested": [{"url": "x?sig=zzz"}, ("bearer abc.def",)], "count": 2, "token_count": 5}
    out = redact_obj(obj)
    assert out["api_key"] == out["access_token"] == out["SAS"] == out["client_credential"] == "***"
    assert out["name"] == "ok"
    assert out["nested"] == [{"url": "x?sig=***"}, ["bearer ***"]]
    assert out["count"] == 2
    assert out["token_count"] == 5  # key pattern is anchored, non-string values are kept


def test_json_formatter_and_trace_redact(tmp_path):
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "got sig=secret", None, None)
    record.data = {"password": "p"}
    payload = json.loads(JsonLineFormatter().format(record))
    assert payload["msg"] == "got sig=***"
    assert payload["data"] == {"password": "***"}
    path = write_trace(tmp_path / "t" / "trace.json", {"token": "abc", "body": f"Bearer {JWT}"})
    assert json.loads(path.read_text(encoding="utf-8")) == {"token": "***", "body": "Bearer ***"}
