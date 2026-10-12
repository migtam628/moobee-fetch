import importlib.util
import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SOURCE = Path(__file__).resolve().parents[1] / 'services' / 'api' / 'app.py'
sys.path.insert(0, str(SOURCE.parent))
spec = importlib.util.spec_from_file_location('moobee_fetch_api', SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setenv('FETCH_USERNAME', 'tester')
    monkeypatch.setenv('FETCH_PASSWORD', 'long-private-staging-password-example-123456')
    monkeypatch.setenv('ARIA2_RPC_SECRET', 'separate-private-RPC-secret-test-123456789')
    monkeypatch.setattr(mod, 'ROOT', tmp_path)
    c = TestClient(mod.app)
    c.auth = ('tester', 'long-private-staging-password-example-123456')
    return c


def test_auth_and_health(client):
    assert client.get('/health').status_code == 200
    assert client.get('/', auth=('tester', 'wrong')).status_code == 401
    assert client.get('/').status_code == 200
    assert 'MooBee Fetch' in client.get('/').text


def test_valid_magnet_accepts_btih_and_rejects_remote_urls():
    identifier = '0123456789ABCDEF0123456789ABCDEF01234567'
    accepted = f'magnet:?xt=urn:btih:{identifier}&dn=Sample+Legal+Video'
    assert mod.magnet(accepted) == (
        'magnet:?xt=urn:btih:' + identifier.lower() + '&dn=Sample%20Legal%20Video'
    )
    # Common real-world magnet extensions are deliberately stripped, never sent
    # as arbitrary tracker/web-seed destinations to the aria2 downloader.
    extended = (accepted + '&tr=http%3A%2F%2F127.0.0.1%3A8000%2Fprivate'
                '&ws=https%3A%2F%2Finternal.example%2Ffile'
                '&xl=1500&as=https%3A%2F%2Fexample.com%2Fother'
                '&so=0:2&custom=untrusted')
    assert mod.magnet(extended) == mod.magnet(accepted)
    assert 'http' not in mod.magnet(extended)
    for payload in ('https://example.com/video.mp4', 'file:///etc/passwd',
                    'magnet:?xt=urn:btih:bad',
                    f'magnet:?xt=urn:btih:{identifier}&dn=../secret',
                    f'magnet:?xt=urn:btih:{identifier}&dn=bad%5Cpath',
                    f'magnet:?xt=urn:btih:{identifier}&xt=urn:btih:{identifier}',
                    f'magnet:?xt=urn:btih:{identifier}\nX-Test: evil'):
        with pytest.raises(ValueError):
            mod.magnet(payload)


def test_magnet_with_optional_trackers_is_only_queued_with_sanitized_uri(client, monkeypatch):
    import httpx
    captured = []

    async def fake_rpc(method, params=None):
        captured.append((method, params))
        return 'a' * 16

    monkeypatch.setattr(mod, 'rpc', fake_rpc)
    identifier = '0123456789abcdef0123456789abcdef01234567'
    raw = (f'magnet:?xt=urn:btih:{identifier}'
           '&dn=Authorized+Sample'
           '&tr=http%3A%2F%2F169.254.169.254%2Flatest%2Fmeta-data')
    response = client.post('/api/transfers', json={'uri': raw})
    assert response.status_code == 202, response.text
    assert captured == [('addUri', [[
        f'magnet:?xt=urn:btih:{identifier}&dn=Authorized%20Sample'
    ], {'dir': '/downloads', 'seed-time': '0'}])]
    assert '169.254.169.254' not in str(captured)


def test_file_browser_rejects_traversal_and_symlink(client, tmp_path):
    (tmp_path / 'hello.mp4').write_bytes(b'test')
    (tmp_path / 'docs').mkdir()
    (tmp_path / 'docs' / 'a.txt').write_text('hi')
    (tmp_path / 'escape').symlink_to('/etc')
    listing = client.get('/api/files')
    assert listing.status_code == 200
    assert [r['name'] for r in listing.json()['items']] == ['docs', 'hello.mp4']
    for value in ('../etc/passwd', '/etc/passwd', 'escape/passwd', '.hidden',
                  'docs/../../etc/passwd'):
        assert client.get('/api/files/download', params={'path': value}).status_code in (400, 403, 404)
    download = client.get('/api/files/download', params={'path': 'hello.mp4'})
    assert download.status_code == 200 and download.content == b'test'
    assert client.get('/api/files', params={'path': 'docs'}).json()['items'][0]['name'] == 'a.txt'


def test_cross_origin_mutations_blocked(client):
    response = client.post('/api/transfers', json={'uri': 'https://example.org'},
                           headers={'Origin': 'http://malicious.example'})
    assert response.status_code == 403
    response = client.post('/api/transfers', json={'uri': 'https://example.org'},
                           headers={'Origin': 'http://testserver'})
    assert response.status_code == 422


def test_api_never_embeds_real_download_urls_in_public_transfer():
    data = mod.public_transfer({
        'gid': 'a' * 16, 'status': 'active', 'completedLength': '56',
        'totalLength': '100', 'downloadSpeed': '2',
        'files': [{'path': '/downloads/legal.mp4', 'uris': [{'uri': 'secret'}]}],
    })
    assert data['name'] == 'legal.mp4'
    assert data['completed'] == 56
    assert 'secret' not in str(data)


def test_transfer_action_requires_auth(client):
    assert client.post('/api/transfers/aaaa/pause').status_code == 404
    assert client.post('/api/transfers/aaaaaaaaaaaaaaaa/unknown').status_code == 404
    assert client.post('/api/transfers/aaaaaaaaaaaaaaaa/pause', auth=('tester','wrong')).status_code == 401
