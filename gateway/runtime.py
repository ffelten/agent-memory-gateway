"""AWS Lambda wiring. No raw requests, responses, configuration, or errors are logged."""
import base64
import importlib
import json
import os
import time

from gateway.service import Gateway

_cached=None
_cached_until=0


def get_gateway():
    global _cached, _cached_until
    if _cached is not None and time.monotonic()<_cached_until:
        return _cached
    import boto3
    from botocore.config import Config
    from gateway.dynamo import DynamoStore
    config=Config(connect_timeout=2,read_timeout=3,retries={'max_attempts':1})
    secret=boto3.client('secretsmanager',config=config).get_secret_value(
        SecretId=os.environ['GATEWAY_CONFIG_SECRET_ARN'])
    settings=json.loads(secret['SecretString'])
    module,fn=os.environ.get('SENSO_ADAPTER_FACTORY','gateway.senso_bridge:create_provider').split(':',1)
    provider=getattr(importlib.import_module(module),fn)(settings.get('senso',{}))
    store=DynamoStore(boto3.resource('dynamodb',config=config).Table(os.environ['GATEWAY_TABLE']))
    _cached=Gateway(store,provider,admin_token_hash=settings['admin_token_sha256'],
                    report_base_url=settings['report_base_url'])
    _cached_until=time.monotonic()+30
    return _cached


def lambda_handler(event,context):
    try:
        app=get_gateway()
        raw=event.get('body') or ''
        if len(raw)>65536:
            body=b'x'*32769
        elif event.get('isBase64Encoded'):
            try:
                body=base64.b64decode(raw,validate=True)
            except (ValueError,TypeError):
                body=b'{invalid-json'
        else:
            body=raw.encode('utf-8')
        method=event.get('requestContext',{}).get('http',{}).get('method',event.get('httpMethod','GET'))
        status,result,headers=app.handle(method,event.get('rawPath',event.get('path','')),
                                         event.get('headers') or {},body)
        return {'statusCode':status,'headers':headers,'isBase64Encoded':False,
                'body':result if isinstance(result,str) else json.dumps(result,separators=(',',':'))}
    except Exception:
        return {'statusCode':503,'headers':{'Content-Type':'application/json','Cache-Control':'no-store'},
                'body':'{"error":"dependency_unavailable"}','isBase64Encoded':False}


def cleanup_revoked(app,limit=10):
    """Retry deletion after local denial is already authoritative."""
    attempted,deleted=0,0
    deadline=time.monotonic()+15
    for candidate in app.store.list('memory'):
        if attempted>=limit or time.monotonic()+3>deadline:
            break
        if candidate.get('state')!='REVOKED' or not candidate.get('delete_pending'):
            continue
        memory=app.store.get('memory',candidate['memory_id'])
        if memory.get('state')!='REVOKED' or not memory.get('node_id'):
            continue
        attempted+=1
        try:
            app.provider.delete(memory['node_id'])
            app.store.put('memory',memory['memory_id'],dict(memory,delete_pending=False),
                          expected_revision=memory['_rev'])
            deleted+=1
        except Exception:
            pass
    return {'attempted':attempted,'deleted':deleted}


def maintenance_handler(event,context):
    try:
        return cleanup_revoked(get_gateway())
    except Exception:
        return {'reason_code':'DEPENDENCY_UNAVAILABLE'}
