import importlib.util
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

SOURCE = Path(__file__).resolve().parents[1] / 'services' / 'api' / 'app.py'
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
    assert mod.magnet(accepted) == accepted
    for payload in ('https://example.com/video.mp4', 'file:///etc/passwd',
                    'magnet:?xt=urn:btih:bad',
                    f'magnet:?xt=urn:btih:{identifier}&tr=http://127.0.0.1:8000/secret',
                    f'magnet:?xt=urn:btih:{identifier}&dn=../secret',
                    f'magnet:?xt=urn:btih:{identifier}\nX-Test: evil'):
        with pytest.raises(ValueError):
            mod.magnet(payload)


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
