"""Tests for the CV Maker API.

Run with:  pytest

OpenRouter is always stubbed; no test makes a real network call.
"""
import json
import os
import sys

import pytest
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('OPENROUTER_API_KEY', 'test-key')

from fastapi.testclient import TestClient  # noqa: E402

import config  # noqa: E402
import openrouter  # noqa: E402
import templates  # noqa: E402
import main  # noqa: E402
from utils import extract_document_text, DocumentError  # noqa: E402

CV = "Jane Doe\nSenior Engineer\n10 years of Python."
JD = "Senior Backend Engineer, Python and cloud."

ANALYSIS = json.dumps({"ats_score": 80, "missing_keywords": [], "strengths": []})
HTML = "<!DOCTYPE html><html><body>cv</body></html>"


class StubResponse:
    def __init__(self, status_code=200, payload=None, text=''):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


def _completion(content):
    return {"choices": [{"message": {"content": content}}]}


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


@pytest.fixture
def stub_openrouter(monkeypatch):
    """Answer every OpenRouter call; record the payloads."""
    calls = []

    def fake_post(url, headers=None, json=None, **kwargs):
        calls.append({"url": url, "headers": headers, "payload": json, "kwargs": kwargs})
        prompt = json['messages'][0]['content']
        content = ANALYSIS if 'Return ONLY the JSON object' in prompt else HTML
        return StubResponse(200, _completion(content))

    monkeypatch.setattr(openrouter.requests, 'post', fake_post)
    return calls


# --- happy paths -----------------------------------------------------------

def test_optimize_cv_returns_html_and_analysis(client, stub_openrouter):
    r = client.post('/optimize-cv', data={'cv_text': CV, 'job_desc_text': JD})
    assert r.status_code == 200
    body = r.json()
    assert body['message'] == 'CV optimized successfully'
    assert body['html_content'] == HTML
    assert body['model_used'] == config.DEFAULT_MODEL
    assert body['analysis']['ats_score'] == 80
    assert len(stub_openrouter) == 2  # analysis, then generation


def test_cover_letter_and_analyze(client, stub_openrouter):
    r = client.post('/generate-cover-letter', data={'cv_text': CV, 'job_desc_text': JD})
    assert r.status_code == 200
    assert r.json()['html_content'] == HTML

    r = client.post('/analyze-cv', data={'cv_text': CV, 'job_desc_text': JD})
    assert r.status_code == 200


# --- model selection -------------------------------------------------------

def test_default_and_explicit_model(client, stub_openrouter):
    client.post('/optimize-cv', data={'cv_text': CV, 'job_desc_text': JD})
    assert stub_openrouter[0]['payload']['model'] == config.DEFAULT_MODEL

    stub_openrouter.clear()
    client.post('/optimize-cv',
                data={'cv_text': CV, 'job_desc_text': JD, 'model': 'deepseek-v4-pro'})
    assert stub_openrouter[0]['payload']['model'] == 'deepseek/deepseek-v4-pro'


@pytest.mark.parametrize('legacy', [
    'gpt-4-turbo', 'gpt-4', 'gpt-3.5-turbo', 'claude-3-opus',
    'claude-3-sonnet', 'claude-3-haiku', 'gemini-pro', 'llama-3', 'mixtral-8x7b',
])
def test_retired_model_keys_still_work(legacy):
    """Paying clients on old model names must not start getting errors."""
    assert config.resolve_model(legacy) == 'openai/gpt-5.6-terra'


def test_unknown_model_falls_back_to_default():
    assert config.resolve_model('does-not-exist') == config.DEFAULT_MODEL
    assert config.resolve_model(None) == config.DEFAULT_MODEL


def test_every_advertised_model_id_is_well_formed():
    for key, meta in config.AVAILABLE_MODELS.items():
        assert '/' in meta['id'], key
        assert meta['name'] and meta['description']
        assert meta['price_per_m']['input'] > 0


# --- upstream failure handling ---------------------------------------------

def test_upstream_500_returns_json_error_not_crash(client, monkeypatch):
    monkeypatch.setattr(openrouter.requests, 'post',
                        lambda *a, **k: StubResponse(500, {'error': {'message': 'boom'}}))
    r = client.post('/optimize-cv', data={'cv_text': CV, 'job_desc_text': JD})
    assert r.status_code == 500
    assert 'error' in r.json()
    assert 'boom' in r.json()['error']


def test_error_body_with_200_status_is_handled(client, monkeypatch):
    """OpenRouter can return 200 with an error body and no choices."""
    monkeypatch.setattr(openrouter.requests, 'post',
                        lambda *a, **k: StubResponse(200, {'error': {'message': 'no credits'}}))
    r = client.post('/optimize-cv', data={'cv_text': CV, 'job_desc_text': JD})
    assert r.status_code == 500
    assert 'no credits' in r.json()['error']


def test_timeout_returns_json_error(client, monkeypatch):
    def raise_timeout(*a, **k):
        raise requests.exceptions.Timeout()
    monkeypatch.setattr(openrouter.requests, 'post', raise_timeout)
    r = client.post('/optimize-cv', data={'cv_text': CV, 'job_desc_text': JD})
    assert r.status_code == 500
    assert 'timed out' in r.json()['error']


def test_every_outbound_call_has_a_timeout(client, stub_openrouter):
    client.post('/optimize-cv', data={'cv_text': CV, 'job_desc_text': JD})
    assert stub_openrouter
    for call in stub_openrouter:
        assert call['kwargs'].get('timeout') == config.OPENROUTER_TIMEOUT


# --- input validation ------------------------------------------------------

@pytest.mark.parametrize('endpoint', ['/optimize-cv', '/generate-cover-letter', '/analyze-cv'])
def test_missing_inputs_return_400(client, endpoint):
    r = client.post(endpoint, data={'job_desc_text': JD})
    assert r.status_code == 400
    assert r.json() == {'error': 'No CV provided (file or text)'}

    r = client.post(endpoint, data={'cv_text': CV})
    assert r.status_code == 400
    assert r.json() == {'error': 'No job description provided (file or text)'}


def test_unsupported_file_type_returns_400(client):
    r = client.post('/optimize-cv', data={'job_desc_text': JD},
                    files={'cv_file': ('cv.exe', b'x', 'application/octet-stream')})
    assert r.status_code == 400
    assert r.json() == {'error': 'Unsupported CV file type. Use PDF, DOCX, or TXT'}


def test_txt_upload_is_read(client, stub_openrouter):
    r = client.post('/optimize-cv', data={'job_desc_text': JD},
                    files={'cv_file': ('cv.txt', CV.encode(), 'text/plain')})
    assert r.status_code == 200
    assert CV in stub_openrouter[0]['payload']['messages'][0]['content']


def test_oversized_upload_rejected(client, monkeypatch):
    monkeypatch.setattr('utils.MAX_UPLOAD_BYTES', 1024)
    big = b'x' * 2048
    r = client.post('/optimize-cv', data={'job_desc_text': JD},
                    files={'cv_file': ('cv.txt', big, 'text/plain')})
    assert r.status_code == 413
    assert 'too large' in r.json()['error']


def test_extract_document_text_messages():
    with pytest.raises(DocumentError) as exc:
        extract_document_text(None, None, 'CV')
    assert exc.value.message == 'No CV provided (file or text)'
    assert extract_document_text(None, 'some text', 'CV') == 'some text'


# --- templates -------------------------------------------------------------

def test_style_selection_matches_legacy_behaviour():
    """modern and minimal were two copies of the same template; keep that."""
    assert templates.get_style_template('minimal') == templates.get_style_template('modern')
    assert templates.get_style_template('unknown') == templates.get_style_template('classic')
    assert templates.get_style_template(None) == templates.get_style_template('classic')
    for style in ('classic', 'modern', 'creative'):
        assert templates.get_style_template(style).lstrip().startswith('<!DOCTYPE html>')


def test_style_reaches_the_prompt(client, stub_openrouter):
    client.post('/optimize-cv',
                data={'cv_text': CV, 'job_desc_text': JD, 'style': 'creative'})
    generation_prompt = stub_openrouter[1]['payload']['messages'][0]['content']
    assert templates.get_style_template('creative') in generation_prompt


# --- info endpoints --------------------------------------------------------

def test_available_models_lists_every_model(client):
    body = client.get('/available-models').json()
    assert set(body['models']) == set(config.AVAILABLE_MODELS)
    assert body['details'][body['default']]['id'] == config.DEFAULT_MODEL


def test_health_reports_status(client, monkeypatch):
    monkeypatch.setattr(main.requests, 'get', lambda *a, **k: StubResponse(200, {}))
    body = client.get('/health').json()
    assert body['status'] == 'healthy'
    assert body['checks']['gotenberg']['reachable'] is True

    def unreachable(*a, **k):
        raise requests.exceptions.ConnectionError('refused')
    monkeypatch.setattr(main.requests, 'get', unreachable)
    r = client.get('/health')
    assert r.status_code == 200
    assert r.json()['status'] == 'degraded'


def test_health_unhealthy_without_api_key(client, monkeypatch):
    monkeypatch.setattr(main, 'OPENROUTER_API_KEY', '')
    r = client.get('/health')
    assert r.status_code == 503
    assert r.json()['status'] == 'unhealthy'


# --- analysis JSON parsing (production incident 2026-08-05) -----------------
# A long CV made the analysis reply exceed max_tokens=2000, so it arrived
# truncated mid-string and the unguarded second json.loads raised a 500.

from utils import parse_model_json  # noqa: E402

TRUNCATED = (
    '```json\n'
    '{\n'
    '  "ats_score": 72,\n'
    '  "keyword_match_percentage": 64,\n'
    '  "missing_keywords": ["kubernetes", "terraform"],\n'
    '  "strengths": ["Deep Python experience", "Led a team of six"],\n'
    '  "recommendations": ["Add a metrics-driven summary", "Mention cloud cost w'
)


def test_parse_model_json_recovers_truncated_reply():
    """The exact shape that took production down: fenced, then cut mid-string."""
    parsed = parse_model_json(TRUNCATED)
    assert parsed is not None
    assert parsed['ats_score'] == 72
    assert parsed['missing_keywords'] == ['kubernetes', 'terraform']
    # the half-written recommendation is dropped, the complete one survives
    assert 'Add a metrics-driven summary' in parsed['recommendations']


@pytest.mark.parametrize('raw,expected', [
    ('{"a": 1}', {'a': 1}),
    ('```json\n{"a": 1}\n```', {'a': 1}),
    ('```\n{"a": 1}\n```', {'a': 1}),
    ('Here is the analysis:\n{"a": 1}\nHope that helps!', {'a': 1}),
    ('{"a": [1, 2], "b": {"c": "d"}}', {'a': [1, 2], 'b': {'c': 'd'}}),
    ('{"a": "say \\"hi\\" now"}', {'a': 'say "hi" now'}),
])
def test_parse_model_json_shapes(raw, expected):
    assert parse_model_json(raw) == expected


@pytest.mark.parametrize('raw', ['', '   ', 'no json here at all', '[1,2,3]', None])
def test_parse_model_json_gives_up_cleanly(raw):
    assert parse_model_json(raw) is None


def test_truncated_analysis_no_longer_500s(client, monkeypatch):
    """/optimize-cv must still return a CV when the analysis comes back broken."""
    def fake_post(url, headers=None, json=None, **kwargs):
        prompt = json['messages'][0]['content']
        if 'Return ONLY the JSON object' in prompt:
            return StubResponse(200, _completion(TRUNCATED))
        return StubResponse(200, _completion(HTML))
    monkeypatch.setattr(openrouter.requests, 'post', fake_post)

    r = client.post('/optimize-cv', data={'cv_text': CV, 'job_desc_text': JD})
    assert r.status_code == 200
    assert r.json()['html_content'] == HTML
    assert r.json()['analysis']['ats_score'] == 72


def test_unsalvageable_analysis_still_returns_a_cv(client, monkeypatch):
    def fake_post(url, headers=None, json=None, **kwargs):
        prompt = json['messages'][0]['content']
        if 'Return ONLY the JSON object' in prompt:
            return StubResponse(200, _completion("I could not analyse that."))
        return StubResponse(200, _completion(HTML))
    monkeypatch.setattr(openrouter.requests, 'post', fake_post)

    r = client.post('/optimize-cv', data={'cv_text': CV, 'job_desc_text': JD})
    assert r.status_code == 200
    assert r.json()['html_content'] == HTML
    assert 'note' in r.json()['analysis']


def test_analysis_uses_raised_token_ceiling(client, stub_openrouter):
    client.post('/optimize-cv', data={'cv_text': CV, 'job_desc_text': JD})
    assert stub_openrouter[0]['payload']['max_tokens'] == config.ANALYSIS_MAX_TOKENS
    assert config.ANALYSIS_MAX_TOKENS > 2000


def test_analyze_cv_reports_unusable_analysis(client, monkeypatch):
    monkeypatch.setattr(openrouter.requests, 'post',
                        lambda *a, **k: StubResponse(200, _completion('not json')))
    r = client.post('/analyze-cv', data={'cv_text': CV, 'job_desc_text': JD})
    assert r.status_code == 502
    assert 'error' in r.json()
