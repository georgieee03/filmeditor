"""MCP response compatibility regressions, independent of media rendering.

Run --live-parser separately to exercise the installed Codex app-server parser.
That opt-in check starts a disposable thread and local fixture, but no model turn.
"""
import contextlib
import copy
import io
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import app


PNG='iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+iBLsAAAAASUVORK5CYII='


def preview_result():
    return {'content':[{'type':'text','text':'Preview completed.'},
        {'type':'image','mimeType':'image/png','data':PNG,
         'annotations':{'audience':['user'],'priority':1.0}}],
        'structuredContent':{'created':True,'path':'/workspace/cache/preview.png','frame':3,
                             'managed_output':True,'size_bytes':68},'isError':False}


def completed_result():
    artifact={'kind':'rendered_media','path':'/workspace/exports/film.mp4',
              'uri':'file:///workspace/exports/film.mp4','mime_type':'video/mp4',
              'size_bytes':200,'revision':'a'*64}
    return {'content':[{'type':'text','text':'Render completed.'},
        {'type':'resource_link','uri':artifact['uri'],'name':'film.mp4','mimeType':'video/mp4',
         'size':200,'annotations':{'audience':['user'],'priority':1.0}}],
        'structuredContent':{'job_id':'render-1','status':'completed','delivery_complete':True,
                             'artifacts':[artifact]},'isError':False}


class McpResponseReviewTests(unittest.TestCase):
    def test_preview_keeps_image_and_structured_payload_without_content_annotations(self):
        original=preview_result(); before=copy.deepcopy(original)
        normalized=app.normalize_mcp_result(original)
        self.assertEqual(original,before)
        self.assertEqual(normalized['structuredContent'],original['structuredContent'])
        self.assertEqual(normalized['content'][1],{'type':'image','mimeType':'image/png','data':PNG})
        self.assertFalse(normalized['isError'])

    def test_completed_export_keeps_artifact_paths_and_resource_link(self):
        original=completed_result(); normalized=app.normalize_mcp_result(original)
        self.assertEqual(normalized['structuredContent'],original['structuredContent'])
        link=normalized['content'][1]
        self.assertEqual(link['type'],'resource_link')
        self.assertEqual(link['uri'],original['structuredContent']['artifacts'][0]['uri'])
        self.assertNotIn('annotations',link)
        self.assertEqual(normalized['content'][0],original['content'][0])

    def test_error_and_domain_annotation_data_keep_their_meaning(self):
        original={'isError':True,'content':[{'type':'text','text':'Revision changed.'}],
                  'structuredContent':{'error':'Revision changed.','annotations':{'important':'project data'}},
                  '_meta':{'annotations':{'important':'transport data'}}}
        normalized=app.normalize_mcp_result(original)
        self.assertEqual(normalized,original)
        normalized['structuredContent']['annotations']['important']='changed'
        self.assertEqual(original['structuredContent']['annotations']['important'],'project data')

    def test_stdio_tools_call_applies_normalization_before_serializing(self):
        request={'jsonrpc':'2.0','id':1,'method':'tools/call',
                 'params':{'name':'render_preview','arguments':{'frame':3}}}
        incoming=SimpleNamespace(buffer=io.BytesIO((json.dumps(request)+'\n').encode()))
        output=io.StringIO(); original=preview_result()
        with patch.object(app,'api_request',return_value=original) as api,patch.object(sys,'stdin',incoming),contextlib.redirect_stdout(output):
            app.mcp(Path('/fixture/workspace'))
        wire=json.loads(output.getvalue())
        self.assertEqual(wire['id'],1)
        self.assertEqual(wire['result'],app.normalize_mcp_result(original))
        self.assertNotIn('annotations',wire['result']['content'][1])
        api.assert_called_once_with(Path('/fixture/workspace'),'/api/call',
                                   {'name':'render_preview','arguments':{'frame':3}})


def live_parser():
    """Observe known upstream failure and corrected success in the actual client."""
    from film.core import ROOT,Workspace
    from film.rpc import JsonlProcess
    payloads={'preview_raw':preview_result(),'completed_raw':completed_result(),
              'preview_normalized':app.normalize_mcp_result(preview_result()),
              'completed_normalized':app.normalize_mcp_result(completed_result())}
    fixture='''import json,sys
payloads='''+repr(payloads)+'''
for line in sys.stdin:
 m=json.loads(line);ident=m.get('id');method=m.get('method');p=m.get('params',{})
 if ident is None:continue
 if method=='initialize':r={'protocolVersion':'2025-06-18','capabilities':{'tools':{}},'serverInfo':{'name':'film_response_probe','version':'1'}}
 elif method=='tools/list':r={'tools':[{'name':name,'description':'Local response parser fixture','inputSchema':{'type':'object','properties':{}},'outputSchema':{'type':'object'}} for name in payloads]}
 elif method=='tools/call':r=payloads[p['name']]
 else:r={}
 print(json.dumps({'jsonrpc':'2.0','id':ident,'result':r}),flush=True)
'''
    rows=[]
    with tempfile.TemporaryDirectory(prefix='filmeditor-mcp-parser-review-') as temporary:
        root=Path(temporary);script=root/'fixture.py';script.write_text(fixture)
        workspace=Workspace(root/'workspace')
        command=[workspace.executable('codex'),'-c','forced_login_method="chatgpt"','-c','model_provider="openai"',
            '-c','mcp_servers.film_response_probe.command='+json.dumps(sys.executable),
            '-c','mcp_servers.film_response_probe.args='+json.dumps(['-B',str(script)]),'app-server']
        rpc=JsonlProcess(command,cwd=root,jsonrpc=False)
        try:
            initialized=rpc.request('initialize',{'clientInfo':{'name':'film-response-review','version':'1'},
                'capabilities':{'experimentalApi':True}},timeout=30)
            rpc.notify('initialized')
            thread=rpc.request('thread/start',{'cwd':str(root),'approvalPolicy':'never','sandbox':'danger-full-access'},timeout=60)['thread']['id']
            for name in payloads:
                try:
                    result=rpc.request('mcpServer/tool/call',{'threadId':thread,'server':'film_response_probe',
                        'tool':name,'arguments':{}},timeout=30)
                    if name.endswith('normalized'):
                        assert result['structuredContent']==payloads[name]['structuredContent']
                        assert [block['type'] for block in result['content']]==[block['type'] for block in payloads[name]['content']]
                    row={'variant':name,'accepted':True,'content_types':[block['type'] for block in result['content']]}
                except Exception as exc:
                    if name.endswith('normalized'):raise
                    row={'variant':name,'accepted':False,'error':str(exc)}
                rows.append(row)
        finally:rpc.close();workspace.close()
    report={'fixture_only':True,'no_model_turn':True,'client':initialized['userAgent'],
            'normalized_rich_results_pass':all(row['accepted'] for row in rows if row['variant'].endswith('normalized')),
            'results':rows}
    destination=ROOT/'reports/rebuild/ai/mcp-normalized-compatibility.json'
    destination.parent.mkdir(parents=True,exist_ok=True)
    destination.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    if sys.argv[1:]==['--live-parser']:live_parser()
    else:unittest.main()
