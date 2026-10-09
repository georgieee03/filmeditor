#!/usr/bin/env python3
"""Launch FilmEditor locally or expose its running workspace as an MCP stdio server."""
from __future__ import annotations
import argparse
import copy
import json
import os
from pathlib import Path
import socket
import stat
import sys
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit
import webbrowser
from film.core import Workspace, atomic, result
from film.server import FilmServer

def normalize_mcp_result(value):
    """Keep media/structured results compatible with the installed Codex client.

    Codex 0.162.0-alpha.2 rejects content-block annotations on image and resource
    links. These optional display hints do not change the tool's actual result.
    Keep the engine response intact for local consumers and normalize only MCP.
    """
    normalized=copy.deepcopy(value)
    for block in normalized.get('content',[]):
        if isinstance(block,dict):block.pop('annotations',None)
    return normalized

def api_request(workspace,route,payload=None):
    """Connect to the current app-owned workspace, using its advertised transport."""
    config=json.loads((workspace/'.film/connection.json').read_text())
    if config.get('transport')=='unix':
        path=Path(config['socket'])
        info=path.stat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode & 0o077:
            raise ValueError('Desktop socket must be private to this user')
        message={'operation':'api','route':route,'project':config['project'],'token':config['token']}
        if payload is not None: message['body']=payload
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as connection:
            connection.settimeout(3700); connection.connect(str(path))
            connection.sendall(json.dumps(message).encode()+b'\n')
            with connection.makefile('rb') as stream: raw=stream.readline(64*1024**2+1)
        if len(raw)>64*1024**2: raise ValueError('Desktop response too large')
        value=json.loads(raw)
        if 'error' in value: raise ValueError(value['error'])
        return value['result']
    u=urlsplit(config['url'])
    if u.scheme!='http' or u.hostname!='127.0.0.1' or not u.port: raise ValueError('Connection must be local loopback')
    req=urllib.request.Request(config['url']+route,data=json.dumps(payload).encode() if payload is not None else None,
        headers={'Authorization':'Bearer '+config['token'],'Content-Type':'application/json'})
    try:
        with urllib.request.urlopen(req,timeout=3700) as response: return json.load(response)
    except urllib.error.HTTPError as exc: raise ValueError(exc.read().decode()[:4000]) from exc

def mcp(workspace):
    def api(route,payload=None):
        return api_request(workspace,route,payload)
    while True:
        line=sys.stdin.buffer.readline(4*1024**2+1)
        if not line: break
        if len(line)>4*1024**2: raise SystemExit('MCP message too large')
        ident=None
        try:
            m=json.loads(line); ident=m.get('id'); method=m.get('method'); p=m.get('params',{})
            if ident is None: continue
            if method=='initialize':
                value={'protocolVersion':'2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'filmeditor','version':'0.1.0'},
                    'instructions':'Use film_assets and installed engine schemas. Preserve originals and pass exact expected_revision for edits.'}
            elif method=='ping': value={}
            elif method=='tools/list': value=api('/api/tools')
            elif method=='tools/call':
                try: value=normalize_mcp_result(api('/api/call',{'name':p['name'],'arguments':p.get('arguments',{})}))
                except Exception as exc: value=result({'error':str(exc)},True)
            else:
                print(json.dumps({'jsonrpc':'2.0','id':ident,'error':{'code':-32601,'message':'Unsupported method'}}),flush=True); continue
            print(json.dumps({'jsonrpc':'2.0','id':ident,'result':value}),flush=True)
        except Exception as exc:
            print(json.dumps({'jsonrpc':'2.0','id':ident,'error':{'code':-32603,'message':'Start FilmEditor first. '+str(exc)}}),flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',nargs='?',choices=['serve','mcp','desktop'],default='serve')
    p.add_argument('--workspace',default=str(Path.home()/'Movies/FilmEditor'))
    p.add_argument('--port',type=int,default=4317); p.add_argument('--no-browser',action='store_true')
    a=p.parse_args(); path=Path(a.workspace).expanduser().resolve()
    if a.mode=='mcp': return mcp(path)
    if a.mode=='desktop':
        from film.desktop import run
        return run(path)
    ws=Workspace(path)
    import fcntl
    lock=(path/'.film/server.lock').open('a')
    try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError: raise SystemExit('This workspace already has a FilmEditor process')
    server=FilmServer(ws,a.port); connection=path/'.film/connection.json'
    atomic(connection,json.dumps({'url':server.origin,'token':server.token}).encode())
    url=server.origin+'/#'+server.token
    print('Private local URL (do not share):\n'+url,flush=True); print('Workspace: '+str(path),flush=True)
    if not a.no_browser: threading.Timer(.5,lambda:webbrowser.open(url)).start()
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally:
        server.library.close(); server.codex.close(); ws.close(); server.server_close(); connection.unlink(missing_ok=True); lock.close()

if __name__=='__main__': main()
