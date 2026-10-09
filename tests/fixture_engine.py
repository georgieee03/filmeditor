#!/usr/bin/env python3
"""TEST FIXTURE ONLY; not Shotcut, HyperFrames, or Codex."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import xml.etree.ElementTree as ET

if len(sys.argv)>1 and sys.argv[1]=='render':
    output=Path(sys.argv[sys.argv.index('--output')+1])
    if os.environ.get('FILM_FIXTURE_RENDER_FAIL'): raise SystemExit(2)
    if os.environ.get('FILM_FIXTURE_RENDER_SLOW'): time.sleep(20)
    output.parent.mkdir(parents=True,exist_ok=True)
    raise SystemExit(subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=c=blue:s=96x160:r=30','-t','0.4',
        '-c:v','libx264','-pix_fmt','yuv420p','-threads','1',str(output)]).returncode)

catalog=[{'name':n,'description':'TEST FIXTURE '+n,'inputSchema':{'type':'object','properties':{},'additionalProperties':True}}
    for n in ['shotcut_status','shotcut_doctor','create_project','inspect_project','edit_project','render_preview','start_render','render_status']]
def emit(value): print(json.dumps(value),flush=True)
def reply(ident,value): emit({'id':ident,'result':value})
def tool(value): return {'content':[{'type':'text','text':json.dumps(value)}]}
for line in sys.stdin:
    m=json.loads(line); method=m.get('method'); ident=m.get('id'); p=m.get('params',{})
    if ident is None: continue
    try:
        if method=='initialize': reply(ident,{'protocolVersion':'2025-06-18','capabilities':{},'serverInfo':{'name':'fixture','version':'test'}})
        elif method=='account/read': reply(ident,{'account':{'type':os.environ.get('FILM_FIXTURE_ACCOUNT','chatgpt'),'planType':'test'}})
        elif method=='model/list': reply(ident,{'data':[{'id':'fixture-model','model':'fixture-model','displayName':'Fixture model','supportedReasoningEfforts':[{'reasoningEffort':'high'}]}]})
        elif method in ('thread/start','thread/resume'):
            assert p['sandbox']=='danger-full-access' and p['approvalPolicy']=='never' and p['modelProvider']=='openai'
            reply(ident,{'thread':{'id':'fixture-thread'}})
        elif method=='turn/start':
            assert p['serviceTierForTurn']=='default' and p['sandboxPolicy']=={'type':'dangerFullAccess'} and p['approvalPolicy']=='never'
            reply(ident,{'turn':{'id':'fixture-turn'}})
            emit({'method':'item/reasoning/summaryTextDelta','params':{'delta':'DO NOT DISPLAY THIS'}})
            emit({'method':'item/agentMessage/delta','params':{'itemId':'fixture-message','delta':'Fixture edit response.'}})
            emit({'method':'item/completed','params':{'item':{'type':'agentMessage','id':'fixture-message','text':'Fixture edit response.'}}})
            emit({'method':'turn/completed','params':{'turn':{'id':'fixture-turn','status':'completed'}}})
        elif method=='turn/interrupt': reply(ident,{})
        elif method=='tools/list': reply(ident,{'tools':catalog})
        elif method=='tools/call':
            n=p['name']; a=p['arguments']
            if n in ('shotcut_status','shotcut_doctor'): out={'fixture':True,'ready':False}
            elif n=='create_project':
                path=Path(a['project_path']); root=ET.Element('mlt')
                ET.SubElement(root,'profile',frame_rate_num=str(a['fps_num']),frame_rate_den=str(a['fps_den']))
                for track in [{'kind':'video','name':'V1'}]+a['tracks']:
                    e=ET.SubElement(root,'playlist',id=track['name']); ET.SubElement(e,'property',name='shotcut:name').text=track['name']
                main=ET.SubElement(root,'tractor',id='tractor'); ET.SubElement(main,'property',name='shotcut').text='1'
                for t in [{'kind':'video','name':'V1'}]+a['tracks']: ET.SubElement(main,'track',producer=t['name'])
                path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(ET.tostring(root)); out={'path':str(path)}
            elif n=='inspect_project': out={'revision':hashlib.sha256(Path(a['path']).read_bytes()).hexdigest(),'fixture':True}
            elif n=='edit_project':
                path=Path(a['project_path']); raw=path.read_bytes()
                assert hashlib.sha256(raw).hexdigest()==a['expected_revision']
                root=ET.fromstring(raw)
                for i,op in enumerate(a['operations']):
                    assert op['op']=='add_clip'; c=ET.SubElement(root,'chain',id='clip'+str(i)); ET.SubElement(c,'property',name='resource').text=op['path']
                    pl=next(e for e in root.findall('playlist') if e.get('id')==op['track'])
                    ET.SubElement(pl,'entry',{'producer':'clip'+str(i),'in':'0','out':'29'})
                path.write_bytes(ET.tostring(root)); out={'revision':hashlib.sha256(path.read_bytes()).hexdigest(),'fixture':True}
            else: out={'fixture':True,'arguments':a}
            reply(ident,tool(out))
        elif method=='sleep': time.sleep(1); reply(ident,{})
        elif method=='crash': raise SystemExit(3)
        else: reply(ident,p)
    except Exception as exc: emit({'id':ident,'error':{'code':-32602,'message':str(exc)}})
