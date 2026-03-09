from pathlib import Path

import pytest

from src.ozon_browser import (
    BrowserCommandError,
    BrowserConfig,
    OzonBrowser,
    _extract_price_rub,
    _normalize_orders_payload,
    _normalize_search_payload,
    main,
)


def test_extract_price_rub_parses_ruble_price() -> None:
    assert _extract_price_rub("Цена 1 299 ₽ со скидкой") == 1299
    assert _extract_price_rub("No price here") is None


def test_normalize_search_payload_compacts_text_and_price() -> None:
    payload = _normalize_search_payload(
        {
            "items": [
                {
                    "title": "  Детский   шампунь ",
                    "url": "https://www.ozon.ru/product/1",
                    "raw_text": "Детский шампунь 699 ₽ быстрая доставка",
                }
            ]
        }
    )

    assert payload["items"][0]["title"] == "Детский шампунь"
    assert payload["items"][0]["price_rub"] == 699


def test_normalize_orders_payload_extracts_order_number_and_status() -> None:
    payload = _normalize_orders_payload(
        {
            "orders": [
                {
                    "raw_text": "Заказ №123ABC Передан в доставку курьером",
                    "url": "https://www.ozon.ru/my/order/123ABC",
                }
            ]
        }
    )

    assert payload["orders"][0]["order_number"] == "123ABC"
    assert payload["orders"][0]["status"] is None


def test_place_order_requires_confirmation(tmp_path: Path) -> None:
    browser = OzonBrowser(
        BrowserConfig(
            repo_root=tmp_path,
            profile_path=tmp_path / "profile",
            download_path=tmp_path / "downloads",
        )
    )

    with pytest.raises(BrowserCommandError):
        browser.place_order(confirm=False)


def test_main_reports_error_json(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys) -> None:
    def fake_resolve(_args):
        return BrowserConfig(
            repo_root=tmp_path,
            profile_path=tmp_path / "profile",
            download_path=tmp_path / "downloads",
        )

    def fake_orders(self):
        raise BrowserCommandError("not logged in")

    monkeypatch.setattr("src.ozon_browser._resolve_config", fake_resolve)
    monkeypatch.setattr(OzonBrowser, "fetch_order_statuses", fake_orders)

    rc = main(["orders"])

    assert rc == 1
    assert "not logged in" in capsys.readouterr().out
