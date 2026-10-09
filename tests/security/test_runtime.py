import base64
import json
from unittest.mock import patch

import pytest

from gateway import runtime
from gateway.senso_bridge import SensoProvider
from adapters.senso_adapter import SensoError


class Adapter:
    def __init__(self):
        self.version = 1
        self.requests = []
    def ingest(self,title,text):
        return {'node_id':'n1','content_id':'c1','status':'processing'}
    def _request(self,method,path):
        self.requests.append((method,path))
        return 200, {'id':'n1','content':{'id':'c1','version_num':self.version,'processing_status':'complete'}}
    def search_context(self,query,content_ids,max_results=5):
        return [{'content_id':'c1','chunk_text':'approved text','score':1},
                {'content_id':'other','chunk_text':'private content'}]
    def delete(self,node_id):
        return True


def test_bridge_uses_actual_provider_version_and_filters_unknown_chunks():
    adapter = Adapter()
    bridge = SensoProvider(adapter)
    doc = bridge.ingest(title='t',text='approved text',external_id='m1')
    assert doc.content_id=='c1' and doc.version=='1' and doc.ready
    passages = bridge.search(query='q',content_ids=['c1'],max_results=5,require_scoped_ids=True)
    assert len(passages)==1 and passages[0].version=='1'
    adapter.version=2
    assert bridge.inspect('n1').version=='2'


def test_missing_provider_version_cannot_be_ready():
    adapter = Adapter()
    adapter._request=lambda *_: (200,{'content':{'id':'c1','processing_status':'complete'}})
    with pytest.raises(ValueError):
        SensoProvider(adapter).inspect('n1')


@pytest.mark.parametrize('status', [404, 429, 500, 502, 503, 504])
def test_ingest_retains_ids_during_transient_inspection_failure_then_can_become_ready(status):
    adapter = Adapter()
    inspect_ready = adapter._request
    def temporarily_unavailable(*args):
        raise SensoError(status, 'request failed')
    adapter._request = temporarily_unavailable
    bridge = SensoProvider(adapter)

    pending = bridge.ingest(title='public procedure', text='approved text', external_id='m1')
    assert pending.node_id == 'n1' and pending.content_id == 'c1'
    assert pending.ready is False and pending.version == ''

    adapter._request = inspect_ready
    ready = bridge.inspect(pending.node_id)
    assert ready.node_id == pending.node_id and ready.content_id == pending.content_id
    assert ready.ready is True and ready.version == '1'


@pytest.mark.parametrize('status', [400, 401, 403])
def test_ingest_does_not_treat_inspection_auth_or_request_errors_as_transient(status):
    adapter = Adapter()
    def denied(*args):
        raise SensoError(status, 'request failed')
    adapter._request = denied
    with pytest.raises(SensoError):
        SensoProvider(adapter).ingest(title='t', text='approved text', external_id='m1')


def test_ingest_identity_mismatch_cannot_become_pending_or_ready():
    adapter = Adapter()
    adapter._request = lambda *_: (200, {'content': {'id': 'different-content', 'version_num': 1, 'processing_status': 'complete'}})
    with pytest.raises(ValueError):
        SensoProvider(adapter).ingest(title='t', text='approved text', external_id='m1')


def test_bridge_empty_scope_never_searches():
    adapter=Adapter()
    adapter.search_context=lambda *_,**kw: pytest.fail('unscoped request')
    assert SensoProvider(adapter).search(query='q',content_ids=[],max_results=5,require_scoped_ids=True)==[]


def test_runtime_passes_bytes_headers_and_serializes_response():
    class App:
        def handle(self,method,path,headers,body):
            assert method=='POST' and path=='/v1/memories'
            assert body==b'{"title":"test"}' and headers['authorization']=='Bearer opaque'
            return 202,{'state':'QUARANTINED'},{'Content-Type':'application/json'}
    event={'version':'2.0','rawPath':'/v1/memories',
           'requestContext':{'http':{'method':'POST'}},'headers':{'authorization':'Bearer opaque'},
           'body':base64.b64encode(b'{"title":"test"}').decode(),'isBase64Encoded':True}
    with patch.object(runtime,'get_gateway',return_value=App()):
        result=runtime.lambda_handler(event,None)
    assert result['statusCode']==202 and json.loads(result['body'])['state']=='QUARANTINED'


def test_runtime_dependency_error_never_echoes_exception():
    with patch.object(runtime,'get_gateway',side_effect=RuntimeError('DEMO_SECRET_ALPHA_2026')):
        result=runtime.lambda_handler({},None)
    assert result['statusCode']==503 and 'DEMO_SECRET' not in json.dumps(result)


def test_senso_factory_stays_lazy_when_no_key():
    from gateway.senso_bridge import create_provider
    provider=create_provider({})
    with pytest.raises(ValueError):
        provider.ingest(title='t',text='safe',external_id='id')
