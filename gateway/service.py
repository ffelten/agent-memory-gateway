"""Authoritative policy boundary. No provider response is an authorization decision."""
import hashlib
import hmac
import html
import json
import re
import secrets
import time
import uuid
from urllib.parse import urlsplit

from gateway.scanner import has_secret
from gateway.store import Conflict, IdempotencyConflict

MAX_BODY = 32768
_ID = r'([A-Za-z0-9_-]{1,128})'


class Denied(Exception):
    def __init__(self, status, error, reason='INVALID_REQUEST'):
        self.status, self.error, self.reason = status, error, reason


def digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _fields(data, required, optional=()):
    if not isinstance(data, dict) or set(data) - set(required) - set(optional) or not set(required) <= set(data):
        raise Denied(400, 'invalid_fields')


def _text(value, maximum=32768):
    if not isinstance(value, str) or not value.strip() or len(value.encode('utf-8')) > maximum:
        raise Denied(400, 'invalid_text')
    return value


def _json(body):
    if len(body) > MAX_BODY:
        raise Denied(413, 'body_too_large')
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate key')
            result[key] = value
        return result
    try:
        value = json.loads(body, object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise Denied(400, 'invalid_json') from None


class Gateway:
    def __init__(self, store, provider, *, admin_token_hash, report_base_url, scanner=None):
        if not re.fullmatch(r'[a-f0-9]{64}', admin_token_hash):
            raise ValueError('admin_token_sha256 must be configured')
        parsed = urlsplit(report_base_url)
        if parsed.scheme not in ('https', 'http') or not parsed.netloc or parsed.query or parsed.fragment:
            raise ValueError('report base URL must be configured')
        if parsed.scheme == 'http' and parsed.hostname not in ('127.0.0.1', 'localhost'):
            raise ValueError('public report base URL must use HTTPS')
        self.store, self.provider = store, provider
        self.admin_token_hash = admin_token_hash
        self.report_base_url = report_base_url.rstrip('/')
        self.scanner = scanner or has_secret

    def handle(self, method, path, headers, body):
        """Return (HTTP status, JSON object or HTML, response headers); never log payloads."""
        started = time.perf_counter()
        trace = uuid.uuid4().hex
        ctx = {'trace_id': trace, 'senso_ms': 0.0, 'run': None, 'memory': None,
               'operation': 'get_memory', 'reason': 'OK', 'deadline': time.monotonic()+15.0}
        response_headers = {'Content-Type': 'application/json', 'Cache-Control': 'no-store',
                            'X-Content-Type-Options': 'nosniff', 'X-Trace-Id': trace}
        try:
            method = method.upper()
            headers = {str(k).lower(): v for k, v in headers.items()}
            route, match, admin, public = self._route(method, path)
            ctx['operation'] = route
            if not public:
                token = headers.get('authorization', '')
                if not isinstance(token, str) or not re.fullmatch(r'Bearer [A-Za-z0-9._~-]{8,256}', token):
                    raise Denied(401, 'unauthorized', 'UNAUTHORIZED')
                token_hash = digest(token[7:])
                if admin:
                    if not hmac.compare_digest(token_hash, self.admin_token_hash):
                        raise Denied(401, 'unauthorized', 'UNAUTHORIZED')
                else:
                    binding = self.store.get('token', token_hash)
                    run = self.store.get('run', binding['run_id']) if binding else None
                    if not run or not run.get('active') or run.get('expires_at', 0) <= time.time():
                        raise Denied(401, 'unauthorized', 'UNAUTHORIZED')
                    principal = self.store.get('principal', run['principal_id'])
                    if not principal or not principal.get('active') or principal['tenant_id'] != run['tenant_id']:
                        raise Denied(401, 'unauthorized', 'UNAUTHORIZED')
                    ctx['run'] = run
            data = _json(body) if method == 'POST' else None
            if route == 'create_run':
                status, result = 200, self._create_run(data)
            elif route == 'create_memory':
                status, result = 202, self._create_memory(ctx, data, headers.get('idempotency-key'))
            elif route == 'get_status':
                status, result = 200, self._status(ctx, match)
            elif route == 'search':
                status, result = 200, self._search(ctx, data)
            elif route == 'get_memory':
                status, result = 200, self._get_memory(ctx, match)
            elif route == 'get_source':
                status, result = 200, self._get_source(ctx, match)
            elif route == 'revoke':
                status, result = 200, self._revoke(ctx, match, data)
            elif route == 'create_report':
                status, result = 200, self._create_report(ctx, data)
            else:
                status, result = 200, self._get_report(match)
                response_headers.update({'Content-Type': 'text/html; charset=utf-8',
                    'Content-Security-Policy': "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"})
        except Denied as error:
            status, result, ctx['reason'] = error.status, {'error': error.error}, error.reason
            if error.reason not in ('OK', 'INVALID_REQUEST', 'UNAUTHORIZED', 'NOT_READABLE'):
                result['reason_codes'] = [error.reason]
        except IdempotencyConflict:
            status, result, ctx['reason'] = 409, {'error': 'idempotency_key_reuse'}, 'INVALID_REQUEST'
        except Conflict:
            status, result, ctx['reason'] = 409, {'error': 'state_changed_retry'}, 'INVALID_REQUEST'
        except Exception:
            # Dependency exceptions can contain request/response bodies. Never stringify them.
            status, result, ctx['reason'] = 503, {'error': 'dependency_unavailable'}, 'DEPENDENCY_UNAVAILABLE'
        if isinstance(result, dict):
            result.setdefault('trace_id', trace)
        total = (time.perf_counter() - started) * 1000
        try:
            from analytics.events import record_event
            run, memory = ctx['run'] or {}, ctx['memory'] or {}
            decision = 'ERROR' if status >= 500 else ('DENY' if status >= 400 else 'ALLOW')
            if isinstance(result, dict) and result.get('state') == 'QUARANTINED':
                decision = 'QUARANTINE'
                ctx['reason'] = (result.get('reason_codes') or ['PROVENANCE_UNKNOWN'])[0]
            record_event(self.store, trace_id=trace, tenant_id=run.get('tenant_id'),
                principal_id=run.get('principal_id'), run_id=run.get('run_id'),
                memory_id=memory.get('memory_id'), application_version=memory.get('application_version'),
                operation=ctx['operation'], transport='http', decision=decision, reason_code=ctx['reason'],
                gate_ms=max(0.0, total-ctx['senso_ms']), senso_ms=ctx['senso_ms'], total_ms=total)
        except Exception:
            # A durable audit failure blocks delivery of protected content.
            return 503, {'error':'audit_unavailable','trace_id':trace}, response_headers
        return status, result, response_headers

    def _route(self, method, path):
        routes = (
            ('POST', r'/v1/admin/runs', 'create_run', True, False),
            ('POST', r'/v1/memories', 'create_memory', False, False),
            ('POST', r'/v1/memories/search', 'search', False, False),
            ('GET', r'/v1/memories/'+_ID+r'/status', 'get_status', False, False),
            ('GET', r'/v1/memories/'+_ID, 'get_memory', False, False),
            ('GET', r'/v1/sources/'+_ID, 'get_source', False, False),
            ('POST', r'/v1/admin/memories/'+_ID+r'/revoke', 'revoke', True, False),
            ('POST', r'/v1/reports', 'create_report', False, False),
            ('GET', r'/reports/'+_ID, 'get_report', False, True),
        )
        for verb, pattern, name, admin, public in routes:
            match = re.fullmatch(pattern, path)
            if verb == method and match:
                return name, match.group(1) if match.groups() else None, admin, public
        raise Denied(404, 'not_found', 'NOT_READABLE')

    def _provider(self, ctx, method, *args, **kwargs):
        # Leave room for one bounded provider call and the final policy/audit writes.
        if time.monotonic()+3.0 > ctx['deadline']:
            raise Denied(503, 'dependency_unavailable', 'TIMEOUT')
        start = time.perf_counter()
        try:
            return getattr(self.provider, method)(*args, **kwargs)
        finally:
            ctx['senso_ms'] += (time.perf_counter() - start) * 1000

    def _scan_secret(self, title, text):
        before = time.monotonic()
        try:
            flagged = self.scanner(title, text)
            if type(flagged) is not bool:
                raise ValueError('invalid scanner result')
        except Exception:
            raise Denied(503, 'scanner_unavailable', 'SCANNER_ERROR') from None
        if time.monotonic()-before > 0.2:
            raise Denied(503, 'scanner_unavailable', 'TIMEOUT')
        return flagged

    def _sources(self, run):
        current = self.store.get('run', run['run_id']) if 'run_id' in run else run
        if not current or not current.get('active') or current.get('expires_at', time.time()+1) <= time.time():
            raise Denied(403, 'source_access_denied', 'SOURCE_ACCESS_DENIED')
        principal = self.store.get('principal', run['principal_id'])
        if not principal or not principal.get('active') or principal['tenant_id'] != run['tenant_id']:
            raise Denied(403, 'source_access_denied', 'SOURCE_ACCESS_DENIED')
        if 'run_id' in run and any(current.get(k) != run.get(k) for k in
                                  ('tenant_id','principal_id','source_ids','source_hashes','audience','publishing')):
            raise Denied(403, 'invalid_run_binding', 'PROVENANCE_UNKNOWN')
        sids = run.get('source_ids')
        if not isinstance(sids, list) or not sids or len(sids) != len(set(sids)):
            raise Denied(403, 'invalid_provenance', 'PROVENANCE_UNKNOWN')
        sources = []
        for sid in sids:
            source = self.store.get('source', sid)
            if not source or source.get('tenant_id') != run['tenant_id'] or not source.get('active'):
                raise Denied(403, 'invalid_provenance', 'PROVENANCE_UNKNOWN')
            if source.get('sha256') != digest(source.get('text','')) or not source.get('retrieved_at'):
                raise Denied(403, 'invalid_provenance', 'PROVENANCE_UNKNOWN')
            if 'source_hashes' in run and run['source_hashes'].get(sid) != source['sha256']:
                raise Denied(403, 'invalid_provenance', 'PROVENANCE_UNKNOWN')
            if not self._reader(source.get('readers', []), run['principal_id']):
                raise Denied(403, 'source_access_denied', 'SOURCE_ACCESS_DENIED')
            sources.append(source)
        return sources

    @staticmethod
    def _reader(readers, principal):
        return '*' in readers or principal in readers

    @staticmethod
    def _audience(sources):
        audience = {'*'}
        for source in sources:
            readers = set(source['readers'])
            if '*' in audience:
                audience = readers
            elif '*' not in readers:
                audience &= readers
        return sorted(audience)

    def _create_run(self, data):
        _fields(data, ('template_id','principal_id'))
        template = self.store.get('template', _text(data['template_id'],128))
        if not template or not template.get('active') or template['principal_id'] != data['principal_id']:
            raise Denied(400, 'unknown_template_or_principal')
        sources = self._sources(template)
        if template.get('publishing') and not all(s.get('public') and s['readers']==['*'] for s in sources):
            raise Denied(403, 'non_public_source', 'SOURCE_ACCESS_DENIED')
        run_id, token = uuid.uuid4().hex, secrets.token_urlsafe(32)
        record = dict(run_id=run_id, tenant_id=template['tenant_id'], principal_id=template['principal_id'],
            source_ids=list(template['source_ids']), source_hashes={s['source_id']:s['sha256'] for s in sources},
            audience=self._audience(sources), publishing=bool(template.get('publishing')),
            active=True, expires_at=int(time.time()+6*3600))
        self.store.put('run',run_id,record,create_only=True)
        self.store.put('token',digest(token),{'run_id':run_id},create_only=True)
        return {'run_id':run_id,'token':token,'source_ids':record['source_ids']}

    def _create_memory(self, ctx, data, key):
        _fields(data, ('title','text'))
        title, text = _text(data['title'],1024), _text(data['text'])
        key = _text(key,128)
        run = ctx['run']
        canonical = json.dumps([title,text], ensure_ascii=False, separators=(',',':'))
        content_hash = digest(canonical)
        idem = digest(json.dumps([run['tenant_id'],run['principal_id'],run['run_id'],key]))
        memory = dict(memory_id=uuid.uuid4().hex, tenant_id=run['tenant_id'], principal_id=run['principal_id'],
            creator_run=run['run_id'], source_ids=run['source_ids'], source_hashes=run['source_hashes'],
            audience=run['audience'], title=title,text=text,content_hash=content_hash,text_sha256=digest(text),
            application_version=1,state='PENDING',reason_codes=[],trace_id=ctx['trace_id'],created_at=int(time.time()))
        memory, created = self.store.create_candidate(memory, idem)
        ctx['memory'] = memory
        if created:
            reason = None
            try:
                self._sources(run)
            except Denied as error:
                reason = error.reason
            if not reason:
                try:
                    if self._scan_secret(title,text):
                        reason = 'SECRET_MATCH'
                except Denied as error:
                    reason = error.reason
            if reason:
                memory = self._transition(memory, 'QUARANTINED', reason_codes=[reason])
            else:
                # Freeze policy-passed state first; INGESTING is never readable.
                memory = self._transition(memory,'INGESTING')
                doc = None
                try:
                    self._sources(run)  # Recheck source permissions immediately before export.
                    doc = self._provider(ctx,'ingest',title=title,text=text,external_id=memory['memory_id'])
                    if not doc.node_id or not doc.content_id:
                        raise ValueError('invalid provider identity')
                    memory = self._transition(memory,'INGESTING',node_id=doc.node_id,
                        content_id=doc.content_id,provider_version=doc.version,reason_codes=[])
                except Conflict:
                    latest = self.store.get('memory',memory['memory_id'])
                    if doc is None or not latest or latest['state'] != 'REVOKED':
                        raise
                    # A revoke can commit before ingest returns its provider node.
                    # Preserve that denial and retain the node for trusted cleanup.
                    memory = self._transition(latest,'REVOKED',node_id=doc.node_id,
                        content_id=doc.content_id,provider_version=doc.version,delete_pending=True)
                except Exception:
                    memory = self._transition(memory,'QUARANTINED',reason_codes=['PROVIDER_UNAVAILABLE'])
        ctx['memory'] = memory
        return self._receipt(memory)

    def _transition(self, memory, state, **changes):
        return self.store.put('memory', memory['memory_id'], dict(memory,state=state,**changes),
                              expected_revision=memory['_rev'])

    @staticmethod
    def _receipt(memory):
        return {k:memory[k] for k in ('memory_id','state','reason_codes','trace_id')}

    def _status(self, ctx, mid):
        memory = self.store.get('memory',mid)
        if not memory or memory['creator_run'] != ctx['run']['run_id'] or memory['tenant_id'] != ctx['run']['tenant_id']:
            raise Denied(404,'not_found','NOT_READABLE')
        ctx['memory'] = memory
        if memory['state'] == 'INGESTING' and memory.get('node_id'):
            try:
                self._sources(ctx['run'])
                doc = self._provider(ctx,'inspect',memory['node_id'])
                if doc.content_id != memory['content_id'] or (memory.get('provider_version') and doc.version != memory['provider_version']):
                    memory = self._transition(memory,'QUARANTINED',reason_codes=['PROVIDER_VERSION_CHANGED'])
                elif doc.ready and doc.version:
                    self._sources(ctx['run'])
                    memory = self._transition(memory,'APPROVED',provider_version=doc.version,reason_codes=[])
            except Denied as error:
                memory = self._transition(memory,'QUARANTINED',reason_codes=[error.reason])
            except Conflict:
                memory = self.store.get('memory',mid)
            except Exception:
                # Bounded transient failure leaves memory unreadable and retryable.
                ctx['reason'] = 'PROVIDER_UNAVAILABLE'
        ctx['memory'] = memory
        return self._receipt(memory)

    def _authorized(self, memory, run):
        if not memory or memory.get('state') != 'APPROVED' or memory['tenant_id'] != run['tenant_id']:
            return False
        if not self._reader(memory.get('audience',[]),run['principal_id']):
            return False
        if not set(memory['source_ids']) <= set(run['source_ids']):
            return False
        try:
            sources = {s['source_id']:s for s in self._sources(run)}
            return all(sid in sources and sources[sid]['sha256']==memory['source_hashes'].get(sid)
                       for sid in memory['source_ids'])
        except Denied:
            return False

    def _version_valid(self, ctx, memory):
        try:
            doc = self._provider(ctx,'inspect',memory['node_id'])
            return bool(doc.ready and doc.content_id==memory['content_id'] and doc.version and
                        doc.version==memory['provider_version'])
        except Denied:
            raise
        except Exception:
            return False

    def _result(self, memory, text=None):
        urls = []
        for sid in memory['source_ids']:
            source = self.store.get('source',sid)
            if source and source.get('url'):
                urls.append(source['url'])
        return {'memory_id':memory['memory_id'],'text':memory['text'] if text is None else text,
                'source_urls':urls,'application_version':memory['application_version']}

    def _get_memory(self, ctx, mid):
        memory = self.store.get('memory',mid)
        if not self._authorized(memory,ctx['run']) or not self._version_valid(ctx,memory):
            raise Denied(404,'not_found','NOT_READABLE')
        latest = self.store.get('memory',mid)
        if not self._authorized(latest,ctx['run']) or latest['_rev'] != memory['_rev']:
            raise Denied(404,'not_found','NOT_READABLE')
        ctx['memory'] = latest
        return self._result(latest)

    def _search(self, ctx, data):
        _fields(data,('query',),('max_results',))
        query, count = _text(data['query'],4096), data.get('max_results',5)
        if type(count) is not int or not 1 <= count <= 5:
            raise Denied(400,'invalid_max_results')
        allowed = {}
        candidates = self.store.list('memory')
        if len(candidates) > 100:
            raise Denied(503, 'dependency_unavailable', 'DEPENDENCY_UNAVAILABLE')
        for candidate in candidates:
            memory = self.store.get('memory',candidate['memory_id'])
            if self._authorized(memory,ctx['run']) and self._version_valid(ctx,memory):
                allowed[memory['content_id']] = memory
        if not allowed:
            return {'results':[]}
        chunks = self._provider(ctx,'search',query=query,content_ids=list(allowed),
                                max_results=count,require_scoped_ids=True)
        results = []
        for chunk in chunks:
            registered = allowed.get(chunk.content_id)
            if not registered or chunk.version != registered['provider_version'] or not isinstance(chunk.text,str):
                continue
            latest = self.store.get('memory',registered['memory_id'])
            if not self._authorized(latest,ctx['run']) or latest['_rev'] != registered['_rev']:
                continue
            if not self._version_valid(ctx,latest):
                continue
            # Provider passages select/rank registered documents, but their free-form
            # bytes are untrusted. Deliver only the immutable, admitted local text.
            if self._scan_secret(latest['title'],latest['text']):
                continue
            results.append(self._result(latest))
            if len(results) >= count:
                break
        # Recheck all selected records once more immediately before delivery.
        results = [r for r in results if self._authorized(self.store.get('memory',r['memory_id']),ctx['run'])]
        return {'results':results}

    def _get_source(self, ctx, sid):
        source = next((s for s in self._sources(ctx['run']) if s['source_id']==sid),None)
        if not source:
            raise Denied(403,'forbidden','SOURCE_ACCESS_DENIED')
        return {'source_id':sid,'text':source['text'],'url':source.get('url')}

    def _revoke(self, ctx, mid, data):
        _fields(data,('reason_code',))
        if data['reason_code'] not in ('REVOKED','TEST','ADMIN_REVOKE','SOURCE_CHANGED','INCIDENT'):
            raise Denied(400,'invalid_reason_code')
        memory = self.store.get('memory',mid)
        if not memory:
            raise Denied(404,'not_found','NOT_READABLE')
        if memory['state'] != 'REVOKED':
            memory = self._transition(memory,'REVOKED',reason_codes=['REVOKED'],
                                      delete_pending=bool(memory.get('node_id')))
        ctx['memory'], ctx['reason'] = memory, 'REVOKED'
        # Deletion is durable queued work; never gate local revocation on provider availability.
        return {'memory_id':mid,'state':'REVOKED'}

    def _public_sources(self, run, source_ids):
        sources = self._sources(run)
        if not run.get('publishing'):
            raise Denied(403,'not_a_publishing_run','SOURCE_ACCESS_DENIED')
        if not all(s.get('public') and s.get('readers')==['*'] for s in sources):
            raise Denied(403,'non_public_source','SOURCE_ACCESS_DENIED')
        if not set(source_ids) <= set(run['source_ids']):
            raise Denied(403,'non_public_source','SOURCE_ACCESS_DENIED')
        selected = [s for s in sources if s['source_id'] in source_ids]
        if any(urlsplit(s.get('url') or '').scheme != 'https' for s in selected):
            raise Denied(403,'invalid_public_source','PROVENANCE_UNKNOWN')
        return selected

    def _create_report(self, ctx, data):
        _fields(data,('title','text','source_ids'))
        title, text, sids = _text(data['title'],1024),_text(data['text']),data['source_ids']
        if not isinstance(sids,list) or not sids or any(not isinstance(s,str) for s in sids) or len(sids)!=len(set(sids)):
            raise Denied(400,'invalid_source_ids')
        self._public_sources(ctx['run'],sids)
        if self._scan_secret(title,text):
            raise Denied(400,'rejected','SECRET_MATCH')
        # Citation selection cannot remove a dependency from the run's context.
        sources = self._public_sources(ctx['run'],ctx['run']['source_ids'])
        rid = uuid.uuid4().hex
        report = dict(report_id=rid,title=title,text=text,source_ids=list(ctx['run']['source_ids']),
            citation_source_ids=sids,
            source_hashes={s['source_id']:s['sha256'] for s in sources},tenant_id=ctx['run']['tenant_id'])
        self.store.put('report',rid,report,create_only=True)
        return {'report_id':rid,'report_url':self.report_base_url+'/reports/'+rid}

    def _get_report(self, rid):
        report = self.store.get('report',rid)
        if not report:
            raise Denied(404,'not_found','NOT_READABLE')
        urls = []
        for sid in report['source_ids']:
            source = self.store.get('source',sid)
            if not source or not source.get('active') or not source.get('public') or source.get('readers')!=['*'] or source['tenant_id']!=report['tenant_id'] or source.get('sha256')!=report['source_hashes'][sid] or digest(source['text'])!=source['sha256']:
                raise Denied(404,'not_found','NOT_READABLE')
            url = source.get('url') or ''
            if urlsplit(url).scheme != 'https':
                raise Denied(404,'not_found','NOT_READABLE')
            if sid in report.get('citation_source_ids',report['source_ids']):
                urls.append(url)
        links = ''.join(f'<li><a rel="noreferrer" href="{html.escape(u,quote=True)}">{html.escape(u)}</a></li>' for u in urls)
        return ('<!doctype html><meta charset="utf-8"><title>'+html.escape(report['title'])+'</title>'
                '<h1>'+html.escape(report['title'])+'</h1><pre>'+html.escape(report['text'])+'</pre>'
                '<h2>Sources</h2><ul>'+links+'</ul>')
