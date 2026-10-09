"""Real gateway behavior; only the external provider is replaced."""
import hashlib
import json
import time
from dataclasses import replace

import pytest

from gateway.provider import Document, Passage
from gateway.service import Gateway
from gateway.store import InMemoryStore


class ProviderFixture:
    def __init__(self):
        self.docs = {}
        self.calls = []
        self.ready = True
        self.on_search = None
        self.extra = []

    def ingest(self, *, title, text, external_id):
        doc = Document('node-' + external_id, 'content-' + external_id, '1', self.ready)
        self.docs[doc.node_id] = (doc, text)
        self.calls.append(('ingest', external_id))
        return doc

    def inspect(self, node_id):
        return self.docs[node_id][0]

    def search(self, *, query, content_ids, max_results, require_scoped_ids):
        assert content_ids and require_scoped_ids is True
        self.calls.append(('search', tuple(content_ids)))
        if self.on_search:
            self.on_search()
        return [Passage(d.content_id, text, d.version) for d, text in self.docs.values()
                if d.content_id in content_ids] + self.extra

    def delete(self, node_id):
        raise TimeoutError('provider deletion is delayed')


@pytest.fixture
def rig():
    store, provider = InMemoryStore(), ProviderFixture()
    for p in ('eng-alpha', 'eng-beta'):
        store.put('principal', p, dict(principal_id=p, tenant_id='tenant-demo', active=True))
    for sid, text, readers, public in (
        ('src-public-runbook', 'Public procedure: power-cycle and sync.', ['*'], True),
        ('src-alpha-private', 'Avery Example, 100 Example Lane, Service Plan A.', ['eng-alpha'], False),
    ):
        store.put('source', sid, dict(source_id=sid, tenant_id='tenant-demo', text=text,
            sha256=hashlib.sha256(text.encode()).hexdigest(), readers=readers, public=public,
            active=True, url='https://docs.senso.ai/docs/knowledge-base' if public else None,
            retrieved_at='2026-10-09T20:00:00Z'))
    for template, principal, sids, publishing in (
        ('tpl-alpha', 'eng-alpha', ['src-public-runbook', 'src-alpha-private'], False),
        ('tpl-beta', 'eng-beta', ['src-public-runbook'], False),
        ('tpl-beta-publish', 'eng-beta', ['src-public-runbook'], True),
    ):
        store.put('template', template, dict(template_id=template, principal_id=principal,
            tenant_id='tenant-demo', source_ids=sids, publishing=publishing, active=True))
    app = Gateway(store, provider, admin_token_hash=hashlib.sha256(b'admin-for-tests').hexdigest(),
                  report_base_url='https://gateway.example')
    def call(method, path, token=None, body=None, key=None):
        headers = {'authorization': 'Bearer ' + token} if token else {}
        if key: headers['idempotency-key'] = key
        return app.handle(method, path, headers, json.dumps(body).encode() if body is not None else b'')
    tokens = {}
    for name, template, principal in (
        ('alpha', 'tpl-alpha', 'eng-alpha'), ('beta', 'tpl-beta', 'eng-beta'),
        ('pub', 'tpl-beta-publish', 'eng-beta')):
        code, body, _ = call('POST', '/v1/admin/runs', 'admin-for-tests',
                            {'template_id': template, 'principal_id': principal})
        assert code == 200, body
        tokens[name] = body['token']
    return app, store, provider, call, tokens


def create(rig, who='alpha', text='Useful summary', title='Incident', key='k1'):
    return rig[3]('POST', '/v1/memories', rig[4][who], {'title': title, 'text': text}, key)


def approve(rig, who='alpha', text='Useful summary', key='k1'):
    status, result, _ = create(rig, who, text, key=key)
    assert status == 202, result
    mid = result['memory_id']
    code, body, _ = rig[3]('GET', f'/v1/memories/{mid}/status', rig[4][who])
    assert code == 200 and body['state'] == 'APPROVED', body
    return mid


@pytest.mark.parametrize('where', ['title', 'text'])
def test_quarantine_before_any_provider_ingestion(rig, where):
    kw = {where: 'DEMO_SECRET_ALPHA_2026'}
    status, result, _ = create(rig, **kw)
    assert status == 202 and result['state'] == 'QUARANTINED'
    assert result['reason_codes'] == ['SECRET_MATCH']
    assert not rig[2].calls
    for who in ('alpha', 'beta'):
        code, body, _ = rig[3]('GET', '/v1/memories/' + result['memory_id'], rig[4][who])
        assert code == 404 and 'text' not in body
    assert 'DEMO_SECRET_ALPHA_2026' not in json.dumps(rig[1].list('event'))


def test_private_context_taints_even_innocent_summary(rig):
    mid = approve(rig, text='Harmless generic procedure')
    assert rig[3]('GET', '/v1/memories/' + mid, rig[4]['alpha'])[0] == 200
    assert rig[3]('GET', '/v1/memories/' + mid, rig[4]['beta'])[0] == 404
    code, body, _ = rig[3]('POST', '/v1/memories/search', rig[4]['beta'], {'query':'procedure'})
    assert code == 200 and body['results'] == []
    assert not any(c[0] == 'search' for c in rig[2].calls)


def test_compilation_not_readable_until_status_refresh(rig):
    rig[2].ready = False
    _, result, _ = create(rig)
    mid = result['memory_id']
    assert result['state'] == 'INGESTING'
    assert rig[3]('GET', '/v1/memories/' + mid, rig[4]['alpha'])[0] == 404
    node = rig[1].get('memory', mid)['node_id']
    doc, text = rig[2].docs[node]
    rig[2].docs[node] = (replace(doc, ready=True), text)
    assert rig[3]('GET', f'/v1/memories/{mid}/status', rig[4]['alpha'])[1]['state'] == 'APPROVED'


def test_provider_scope_filter_and_recheck_after_search(rig):
    mid = approve(rig, who='pub', text='Public procedure')
    rig[2].extra = [Passage('unknown-content', 'DEMO_SECRET_ALPHA_2026', '1')]
    code, body, _ = rig[3]('POST', '/v1/memories/search', rig[4]['beta'], {'query':'procedure'})
    assert code == 200 and len(body['results']) == 1
    assert 'DEMO_SECRET' not in json.dumps(body)
    def revoke_during_search():
        m = rig[1].get('memory', mid)
        rig[1].put('memory', mid, dict(m, state='REVOKED'), expected_revision=m['_rev'])
    rig[2].on_search = revoke_during_search
    assert rig[3]('POST', '/v1/memories/search', rig[4]['beta'], {'query':'procedure'})[1]['results'] == []


def test_current_source_permissions_override_existing_memory(rig):
    mid = approve(rig)
    s = rig[1].get('source', 'src-alpha-private')
    rig[1].put('source', s['source_id'], dict(s, readers=[]), expected_revision=s['_rev'])
    assert rig[3]('GET', '/v1/memories/' + mid, rig[4]['alpha'])[0] == 404


def test_revocation_immediate_despite_provider_delete_failure(rig):
    mid = approve(rig)
    status, body, _ = rig[3]('POST', f'/v1/admin/memories/{mid}/revoke', 'admin-for-tests',
                            {'reason_code':'REVOKED'})
    assert status == 200 and body['state'] == 'REVOKED'
    assert rig[3]('GET', '/v1/memories/' + mid, rig[4]['alpha'])[0] == 404
    assert rig[1].get('memory', mid)['delete_pending'] is True


def test_provider_version_drift_fails_closed(rig):
    mid = approve(rig)
    node = rig[1].get('memory', mid)['node_id']
    doc, text = rig[2].docs[node]
    rig[2].docs[node] = (replace(doc, version='2'), text)
    assert rig[3]('GET', '/v1/memories/' + mid, rig[4]['alpha'])[0] == 404


def test_unknown_provenance_and_scanner_failure_quarantine(rig):
    rig[0].scanner = lambda *args: (_ for _ in ()).throw(RuntimeError('sensitive failure'))
    _, body, _ = create(rig)
    assert body['state'] == 'QUARANTINED' and body['reason_codes'] == ['SCANNER_ERROR']
    assert not rig[2].calls
    rig[0].scanner = lambda *args: False
    s = rig[1].get('source', 'src-alpha-private')
    rig[1].put('source', s['source_id'], dict(s, sha256='invalid'), expected_revision=s['_rev'])
    _, body, _ = create(rig, key='k2')
    assert body['state'] == 'QUARANTINED'
    assert not rig[2].calls


def test_idempotency_is_bound_to_run_and_unambiguous_candidate(rig):
    a = create(rig, title='ab', text='c')[1]
    b = create(rig, title='ab', text='c')[1]
    assert a['memory_id'] == b['memory_id']
    assert create(rig, title='a', text='bc')[0] == 409
    c = create(rig, who='beta', title='ab', text='c')[1]
    assert c['memory_id'] != a['memory_id']


def test_authentication_before_parsing_and_body_limit(rig):
    assert rig[0].handle('POST','/v1/memories',{},b'{broken')[0] == 401
    headers = {'authorization':'Bearer ' + rig[4]['alpha']}
    assert rig[0].handle('POST','/v1/memories',headers,b'x'*32769)[0] == 413
    assert rig[3]('POST','/v1/memories',rig[4]['alpha'],
                  {'title':'t','text':'x','approved':True},'a')[0] == 400
    assert rig[3]('POST','/v1/memories/search',rig[4]['alpha'],
                  {'query':'x','max_results':True})[0] == 400


def test_publication_requires_full_public_run_and_escapes_html(rig):
    call, tokens = rig[3:]
    payload = {'title':'Note','text':'<script>alert(1)</script>','source_ids':['src-public-runbook']}
    assert call('POST','/v1/reports',tokens['alpha'],payload)[0] == 403
    assert call('POST','/v1/reports',tokens['beta'],payload)[0] == 403
    code, body, _ = call('POST','/v1/reports',tokens['pub'],payload)
    assert code == 200 and body['report_url'].startswith('https://gateway.example/reports/')
    code, page, headers = call('GET','/reports/' + body['report_id'])
    assert code == 200 and '<script>' not in page and '&lt;script&gt;' in page
    payload['text'] = 'DEMO_SECRET_ALPHA_2026'
    assert call('POST','/v1/reports',tokens['pub'],payload)[0] == 400


def test_revoked_source_hides_previously_published_note(rig):
    _, body, _ = rig[3]('POST','/v1/reports',rig[4]['pub'],
                         {'title':'t','text':'Public procedure','source_ids':['src-public-runbook']})
    s = rig[1].get('source','src-public-runbook')
    rig[1].put('source', s['source_id'], dict(s, public=False), expected_revision=s['_rev'])
    assert rig[3]('GET','/reports/'+body['report_id'])[0] == 404
