"""CSRF fast-path tests: strictly offline, no provider requests."""
from types import SimpleNamespace
import threading
from unittest.mock import Mock

import pytest
import requests

from src.everai.client import EverAIClient, EverAICancelled


class Cookies(dict):
    def set(self, key, value, **kwargs):
        self[key] = value


def make_client(prefer=True):
    client = EverAIClient.__new__(EverAIClient)
    client.prefer_csrf_head = prefer
    client.cancel_event = threading.Event()
    client.timeout = 3
    client.log = Mock()
    client.session = SimpleNamespace(cookies=Cookies(), head=Mock(), get=Mock())
    client.session.get.return_value = SimpleNamespace(
        status_code=200, cookies={'csrftoken': 'offline-get'}, text='')
    return client


def test_head_csrf_skips_html():
    client = make_client()
    client.session.head.return_value = SimpleNamespace(
        status_code=200, cookies={'csrftoken': 'offline-head'})
    assert client.fetch_csrf_token() == 'offline-head'
    client.session.get.assert_not_called()
    assert client.session.cookies['csrftoken'] == 'offline-head'


@pytest.mark.parametrize('response', [
    SimpleNamespace(status_code=405, cookies={}),
    SimpleNamespace(status_code=200, cookies={}),
    requests.ConnectionError('offline'),
])
def test_head_falls_back_to_same_session_get(response):
    client = make_client()
    if isinstance(response, Exception):
        client.session.head.side_effect = response
    else:
        client.session.head.return_value = response
    assert client.fetch_csrf_token() == 'offline-get'
    assert client.session.head.call_count == client.session.get.call_count == 1


def test_legacy_text_path_does_not_use_head():
    client = make_client(False)
    assert client.fetch_csrf_token() == 'offline-get'
    client.session.head.assert_not_called()


def test_cancel_prevents_head_and_get():
    client = make_client()
    client.cancel_event.set()
    with pytest.raises(EverAICancelled):
        client.fetch_csrf_token()
    client.session.head.assert_not_called()
    client.session.get.assert_not_called()
