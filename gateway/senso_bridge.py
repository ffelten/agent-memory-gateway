"""Policy-facing bridge around Florian's adapter; version checks stay trusted."""
import json
import re
import urllib.error
import urllib.request
from urllib.parse import quote

from adapters.senso_adapter import SensoAdapter, SensoError
from gateway.provider import Document, Passage

SENSO_URL = 'https://apiv2.senso.ai/api/v1'


class ProviderUnavailable(Exception):
    """Safe diagnostic without vendor response text or credentials."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class BoundedSensoAdapter(SensoAdapter):
    """Reuse the teammate's API mapping, constrain its credential-bearing transport."""
    def _request(self, method, path, body=None):
        if not path.startswith('/org/') or '?' in path or '#' in path:
            raise ProviderUnavailable('invalid provider path')
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base_url+path, data=data, method=method,
            headers={'X-API-Key':self._api_key,'Accept':'application/json',
                     'Content-Type':'application/json','User-Agent':'agent-memory-gateway/0.1'})
        try:
            with urllib.request.build_opener(NoRedirect()).open(request,timeout=3) as response:
                raw = response.read(1024*1024+1)
                if len(raw)>1024*1024:
                    raise ProviderUnavailable('provider response too large')
                value=json.loads(raw) if raw else {}
                if not isinstance(value,(dict,list)):
                    raise ProviderUnavailable('invalid provider response')
                return response.status,value
        except urllib.error.HTTPError as error:
            # Preserve adapter's scoped 404 fallback/deletion handling, never server text.
            raise SensoError(error.code,'request failed') from None
        except Exception:
            raise ProviderUnavailable('provider unavailable') from None


class SensoProvider:
    def __init__(self, adapter):
        self.adapter=adapter
        self.nodes={}

    def ingest(self, *, title, text, external_id):
        result=self.adapter.ingest(title,text)
        node_id,content_id=result.get('node_id'),result.get('content_id')
        if not isinstance(node_id,str) or not node_id or not isinstance(content_id,str) or not content_id:
            raise ValueError('provider identifiers missing')
        self.nodes[content_id]=node_id
        try:
            doc=self.inspect(node_id)
            if doc.content_id!=content_id:
                raise ValueError('provider identity changed')
            return doc
        except ProviderUnavailable:
            return Document(node_id,content_id,'',False)
        except SensoError as error:
            # Ingestion already returned stable IDs. A temporarily unavailable
            # node must stay INGESTING so status polling can inspect it again.
            if error.status in (404,429) or 500 <= error.status < 600:
                return Document(node_id,content_id,'',False)
            raise

    def inspect(self, node_id):
        if not isinstance(node_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,256}',node_id):
            raise ValueError('invalid provider node ID')
        _,result=self.adapter._request('GET','/org/kb/nodes/'+quote(node_id,safe=''))
        content=result.get('content')
        if not isinstance(content,dict):
            raise ProviderUnavailable('provider compilation metadata unavailable')
        cid=content.get('id')
        version=content.get('version_num')
        ready=content.get('processing_status')=='complete'
        if not isinstance(cid,str) or not cid:
            raise ProviderUnavailable('provider content ID unavailable')
        if version is None and not ready:
            normalized=''
        elif type(version) is int and version>=0:
            normalized=str(version)
        elif isinstance(version,str) and version.isdigit():
            normalized=version
        else:
            raise ValueError('provider version unavailable')
        self.nodes[cid]=node_id
        return Document(node_id,cid,normalized,ready)

    def search(self, *, query, content_ids, max_results, require_scoped_ids=True):
        if require_scoped_ids is not True:
            raise ValueError('scoped provider search required')
        if not content_ids:
            return []
        if not set(content_ids)<=set(self.nodes):
            raise ValueError('unregistered provider search scope')
        rows=self.adapter.search_context(query,content_ids,max_results=max_results)
        result=[]
        for row in rows:
            cid=row.get('content_id')
            if cid not in content_ids:
                continue
            doc=self.inspect(self.nodes[cid])
            text=row.get('chunk_text')
            if doc.ready and doc.content_id==cid and isinstance(text,str):
                result.append(Passage(cid,text,doc.version))
        return result

    def delete(self,node_id):
        self.adapter.delete(node_id)


class LazyProvider:
    """Authentication/quarantine remain available before sponsor keys are installed."""
    def __init__(self,config):
        self.config=config
        self.instance=None
    def __getattr__(self,name):
        if name not in ('ingest','inspect','search','delete'):
            raise AttributeError(name)
        if self.instance is None:
            key,folder=self.config.get('api_key'),self.config.get('folder_id')
            if not isinstance(key,str) or not key or not isinstance(folder,str) or not folder:
                raise ValueError('Senso configuration unavailable')
            base_url=self.config.get('base_url',SENSO_URL)
            if base_url!=SENSO_URL:
                raise ValueError('unapproved Senso origin')
            self.instance=SensoProvider(BoundedSensoAdapter(key,folder,base_url=base_url,timeout=3))
        return getattr(self.instance,name)


def create_provider(config):
    return LazyProvider(config)
