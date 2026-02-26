from unittest.mock import Mock

from src.metrics import start_metrics_server


def test_metrics_server_disabled_when_port_zero(monkeypatch):
    start_mock = Mock()
    monkeypatch.setattr("src.metrics.start_http_server", start_mock)

    start_metrics_server(0)

    start_mock.assert_not_called()


def test_metrics_server_bind_error_is_non_fatal(monkeypatch):
    start_mock = Mock(side_effect=PermissionError("Operation not permitted"))
    monkeypatch.setattr("src.metrics.start_http_server", start_mock)

    start_metrics_server(9101)

    start_mock.assert_called_once_with(9101)
