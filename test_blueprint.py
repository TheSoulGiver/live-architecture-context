import copy
import http.client
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import archctx
import archctx_blueprint as blueprint
from tools import archify as launcher


class BlueprintTest(unittest.TestCase):
    def test_page_swaps_accepted_identity_only_after_current_frame_is_ready(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is needed for the real PAGE script state check")
        # Controlled DOM/transport fixture, not browser layout or rendering evidence.
        script = blueprint.PAGE.split("<script>", 1)[1].split("</script>", 1)[0]
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs');
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.style={};this.textContent='';this.contentWindow={messages:[],postMessage(value){this.messages.push(value)}};}
 append(...items){this.children.push(...items)} replaceChildren(...items){this.children=items}
 insertBefore(item,before){let at=this.children.indexOf(before);this.children.splice(at<0?this.children.length:at,0,item)}
 querySelector(tag){return this.children.find(x=>x.tag===tag)||null}
 setAttribute(name,value){this[name]=value} removeAttribute(name){delete this[name]}
 cloneNode(){return new Element(this.tag)} remove(){this.removed=true}
}
const elements={},timers=[],get=id=>elements[id]??=(new Element()),packet=id=>({status:'FRESH',context_hash:id,
 generation:id,revision:id,artifacts:['current.html','comparison.html'],components:[{id:'service',purpose:'purpose-'+id,
 evidence:[{path:'service.py',line:1}]}],relations:[],development:{observation_id:'obs-'+id,observer_running:true,changes:[]}});
let next=packet('accepted-A'),message;
const context=vm.createContext({TextEncoder,document:{getElementById:get,createElement:tag=>new Element(tag)},
 window:{addEventListener:(_name,handler)=>{message=handler}},fetch:async()=>({status:200,ok:true,json:async()=>next,headers:{get:()=>next.context_hash}}),
 setTimeout:(fn,ms)=>{let timer={fn,ms};timers.push(timer);return timer},clearTimeout:timer=>{if(timer)timer.cancelled=true}});
const read=expression=>vm.runInContext(expression,context),ready=frame=>message({source:frame.contentWindow,data:{kind:'lac-ready'}});
function displayed(id,frame){
 assert.equal(read('data.context_hash'),id);assert.equal(read('picture'),frame);assert.ok(get('identity').textContent.includes(id));
 assert.ok(get('detail').children.some(x=>x.textContent==='purpose-'+id));
 assert.ok(get('detail').querySelector('details').children.some(x=>x.href?.includes('context='+id)));
 assert.equal(frame.contentWindow.messages.at(-1).context,id);
}
(async()=>{
 vm.runInContext(fs.readFileSync(0,'utf8'),context);await new Promise(setImmediate);
 const first=read('loading');assert.equal(read('data'),null);ready(first);read("select('service')");displayed('accepted-A',first);
 next=packet('accepted-B');await context.poll();const delayed=read('loading');assert.notEqual(delayed,first);displayed('accepted-A',first);
 timers.findLast(t=>t.ms===8000&&!t.cancelled).fn();assert.equal(read('loading'),null);assert.ok(delayed.removed);displayed('accepted-A',first);
 assert.ok(get('reason').textContent.includes('加载失败'));
 await context.poll();const second=read('loading');ready(second);assert.ok(first.removed);displayed('accepted-B',second);
 assert.equal(read('version'),'accepted-B:current.html');
 get('delta').onclick();const abandoned=read('loading');assert.ok(abandoned.src.endsWith('/comparison.html'));
 get('now').onclick();assert.equal(read('loading'),null);assert.ok(abandoned.removed);ready(abandoned);
 assert.equal(read('mode'),'now');assert.equal(read('version'),'accepted-B:current.html');displayed('accepted-B',second);
 next=packet('accepted-C');next.components.push(...['db','app','working-only'].map(id=>({id,evidence:[]})));
 next.relations=[{from:'app',to:'service',kind:'custom-unclassified'}];
 const scope={direct_components:['service'],dependencies:['db'],dependents:['app'],uncovered_files:['extra.py'],
  relations:[{id:'raw-edge',from:'service',to:'db',kind:'custom-unclassified',dependency:'unclassified',semantics:'unclassified',
   evidence_state:'accepted_source_evidence',evidence:[{path:'wiring.py',line:8}],omitted_evidence:3}],associations:[{component:'service',paths:['service.py']}],omitted:{dependencies:2}};
 next.development.pending={changed_components:['working-only']};
 next.development.changes=[{path:'service.py',kind:'modify',components:['legacy-only'],impacted_components:['legacy-only'],
  change_scope:{contract:'declared_change_scope/v1',accepted_context_hash:'accepted-C',working_config_hash:'working-config',freshness:'stale',
   accepted:scope,working:{...scope,direct_components:['working-only'],dependencies:['app'],dependents:['db'],omitted:{}},
   limitations:['Synthetic unclassified relation limit'],detail_query:'impact --details'}}];
 next.development.changes.push({path:'other.py',kind:'modify',components:[],change_scope:{contract:'declared_change_scope/v1',
  accepted_context_hash:'accepted-C',freshness:'stale',accepted:{direct_components:['db'],dependencies:[],dependents:[],config_seeds:['db']},working:null}});
 await context.poll();ready(read('loading'));read("select('service')");
 const id=value=>'c-'+Buffer.from(value).toString('hex'),last=()=>read('picture').contentWindow.messages.at(-1),
  fileCard=path=>get('changes').children.find(c=>c.children.some(x=>x.tag==='code'&&x.textContent===path)),
  focus=path=>fileCard(path).children.find(x=>x.tag==='button'&&x.textContent==='图中查看此修改').onclick();
 assert.deepEqual(Array.from(last().direct),[id('service'),id('db')]);assert.ok(get('map-caption').textContent.includes('全部已保存修改'));
 focus('service.py');assert.equal(read('selectedChange'),'service.py');assert.ok(get('map-caption').textContent.includes('修改范围：service.py'));
 assert.deepEqual(Array.from(last().direct),[id('service')]);
 get('all-changes').onclick();assert.equal(read('selectedChange'),null);assert.deepEqual(Array.from(last().direct),[id('service'),id('db')]);
 assert.ok(get('map-caption').textContent.includes('全部已保存修改'));assert.equal(fileCard('other.py').className.includes('unmapped'),false);
 focus('service.py');const sent=last();
 assert.deepEqual(Array.from(sent.direct),[id('service')]);assert.deepEqual(Array.from(sent.dependencies),[id('db')]);assert.deepEqual(Array.from(sent.dependents),[id('app')]);
 for(const legacy of ['indirect','pending','relations'])assert.equal(legacy in sent,false);
 const text=node=>[node.textContent,...node.children.map(text)].join('\n'),body=text(get('changes'));
 for(const expected of ['直接涉及','依赖对象','依赖者','工作区声明 · 尚未接受','working-only','未分类；不据此推断依赖',
  'wiring.py:8','另有 3 个锚点；使用 --details','已接受源码锚点','dependencies 2','extra.py','Synthetic unclassified relation limit','impact --details'])assert.ok(body.includes(expected),expected);
 assert.ok(text(get('detail')).includes('原始入向关系 · 不等于依赖者'));
 next=JSON.parse(JSON.stringify(next));next.development.observation_id='scope-invalid';next.development.changes[0].change_scope.working={error:'Synthetic invalid working definition'};
 await context.poll();assert.ok(text(get('changes')).includes('Synthetic invalid working definition'));
 next=JSON.parse(JSON.stringify(next));next.development.observation_id='scope-unchanged';next.development.changes[0].change_scope.working=null;
 await context.poll();assert.equal(text(get('changes')).includes('工作区声明 · 尚未接受'),false);
 next=JSON.parse(JSON.stringify(next));next.development.observation_id='scope-mismatch';next.development.changes[0].change_scope.accepted_context_hash='other-accepted';
 await context.poll();assert.deepEqual(Array.from(read('picture').contentWindow.messages.at(-1).direct),[]);assert.ok(text(get('changes')).includes('共享范围暂不可用或版本已变'));
 next=JSON.parse(JSON.stringify(next));next.development.observation_id='scope-retained';Object.assign(next.development.changes[0].change_scope,
  {accepted_context_hash:'accepted-C',observation_state:'retained_previous_observation',freshness:'stale',working_config_hash:null,working:{error:'Current inputs unavailable; previous observation retained'}});
 await context.poll();assert.ok(text(get('changes')).includes('保留上次观察，当前修改范围未知'));assert.deepEqual(Array.from(last().direct),[]);
 next=JSON.parse(JSON.stringify(next));next.development.observation_id='scope-path-gone';next.development.changes.shift();
 await context.poll();assert.equal(read('selectedChange'),null);assert.deepEqual(Array.from(last().direct),[id('db')]);assert.ok(get('map-caption').textContent.includes('全部已保存修改'));
 console.log('PASS: frame identity, single/all file scope, accepted-only overlay, retained/invalid scope, witnesses and omissions');
})().catch(error=>{console.error(error);process.exitCode=1});
"""
        result = subprocess.run([node, "-e", harness], input=script, text=True, encoding="utf-8", capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_map_bridge_restores_camera_atomically_and_requires_svg(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is needed for the real MAP_BRIDGE script check")
        script = blueprint.MAP_BRIDGE.split("<script>", 1)[1].split("</script>", 1)[0]
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm'),source=require('node:fs').readFileSync(0,'utf8');
for(const available of [false,true]){
 const events={},messages=[],calls=[],attributes={},parent={postMessage:value=>messages.push(value)};
 const nodes=['service','db','app','working'].map(id=>({dataset:{nodeId:id},classes:{},addEventListener(){},classList:{toggle(name,value){nodes.find(n=>n.classList===this).classes[name]=value}}}));
 const context=vm.createContext({TextEncoder,parent,document:{documentElement:{setAttribute:(k,v)=>attributes[k]=v},
  querySelector:()=>available?{parentElement:{removeAttribute:k=>attributes.removed=k},querySelectorAll:()=>[]}:null,querySelectorAll:()=>nodes},
  window:{addEventListener:(name,fn)=>events[name]=fn},MutationObserver:class{observe(){}},
  Archify:{view:{centerAt:(...args)=>calls.push(args),zoomIn:()=>assert.fail('restore must not race separate zoom animations')}},
  setTimeout(){},clearTimeout(){}});
 vm.runInContext(source,context);
 assert.equal(messages.length,available?1:0);if(!available){assert.equal(events.message,undefined);continue}
 assert.equal(messages[0].kind,'lac-ready');assert.equal(attributes['data-embed'],'true');assert.equal(attributes.removed,'data-wide-diagram');
 const restore=scale=>events.message({source:parent,data:{kind:'lac-map',restore:{scale,viewport:{x:10,y:20,width:100,height:200}}}});
 restore(2.4);assert.equal(calls.length,1);assert.equal(calls[0][0],60);assert.equal(calls[0][1],120);
 assert.equal(calls[0][2].scale,2.4);assert.equal(calls[0][2].instant,true);
 for(const invalid of [NaN,0,3.1])restore(invalid);assert.equal(calls.length,1);
 events.message({source:parent,data:{kind:'lac-map',direct:['service'],dependencies:['service','db'],dependents:['app'],pending:['working'],indirect:['working']}});
 assert.equal(nodes[0].classes['lac-direct'],true);assert.equal(nodes[0].classes['lac-dependency'],false);
 assert.equal(nodes[1].classes['lac-dependency'],true);assert.equal(nodes[2].classes['lac-dependent'],true);
 assert.equal(Object.values(nodes[3].classes).some(Boolean),false);
}
console.log('PASS: camera restore passes scale in one centerAt call; absent SVG never announces ready');
"""
        result = subprocess.run([node, "-e", harness], input=script, text=True, encoding="utf-8", capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_renderer_receipts_and_missing_before_recovery(self):
        # Synthetic renderer contract, not a claim of real Archify rendering.
        for scenario in ("valid", "wrong_delivery", "wrong_comparison", "missing_before"):
            with self.subTest(scenario=scenario), tempfile.TemporaryDirectory() as temporary:
                repo = Path(temporary)
                directory = repo / ".archctx"
                generation = directory / "generations" / ("a" * 16 + "-" + "b" * 32)
                generation.mkdir(parents=True)
                ir = generation / "architecture.archify.json"
                ir.write_text("{}", encoding="utf-8")
                (repo / "view.json").write_text("{}", encoding="utf-8")
                config = {"archify": {"view": "view.json",
                          "render": ["render", "{archify_output}", "{archify_html}"],
                          "compare": ["compare", "{archify_before}", "{archify_output}", "{archify_compare}", "{archify_receipt}"]}}
                candidate = {"revision": "revision", "components": [{"id": "service"}], "relations": []}
                previous = None if scenario != "missing_before" else {"context": candidate, "context_hash": "old",
                            "archify": {"generation": str(generation), "ir": str(generation / "missing.json"), "ir_sha256": "old"}}

                def external(command, _repo, _timeout, values):
                    name = "{archify_html}" if command[0] == "render" else "{archify_compare}"
                    output = Path(values[name])
                    output.write_text("<svg>fixture</svg>", encoding="utf-8")
                    digest = archctx.sha(ir.read_bytes())
                    result = {"ok": True, "artifact": {"sha256": archctx.sha(output.read_bytes())}}
                    if command[0] == "render":
                        result["specification"] = {"sha256": "wrong" if scenario == "wrong_delivery" else digest}
                    else:
                        result.update(base={"rawSha256": digest}, head={"rawSha256": "wrong" if scenario == "wrong_comparison" else digest},
                                      validation={"checkCount": 1, "checksPassed": 1})
                        archctx.atomic(Path(values["{archify_receipt}"]), result)
                    return command, subprocess.CompletedProcess(command, 0, json.dumps(result), "")

                with patch.object(archctx, "run", side_effect=external):
                    if scenario.startswith("wrong"):
                        with self.assertRaisesRegex(ValueError, "receipt does not bind"):
                            blueprint.render_bundle(config, repo, directory, generation, ir, candidate, previous)
                    else:
                        receipt = blueprint.render_bundle(config, repo, directory, generation, ir, candidate, previous)
                        self.assertEqual(receipt["context_hash"], archctx.semantic(candidate))
                        self.assertFalse(receipt["before_available"])
                        if scenario == "missing_before":
                            self.assertIn("unavailable", receipt["before_reason"])

    def test_renderer_rejects_unsupported_node_before_setup(self):
        with patch.object(launcher.shutil, "which", side_effect=["git", "node"]), patch.object(
                launcher.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "v16.20.2\n", "")):
            with self.assertRaisesRegex(RuntimeError, "Node.js 18"):
                launcher.main()

    def test_definition_identity_separates_implementation_from_architecture(self):
        context = {"revision": "a", "components": [{"id": "service", "truth_sources": ["service.py"], "evidence": [{"line": 2, "sha256": "old"}]}],
                   "relations": [{"from": "service", "to": "service", "kind": "calls", "evidence": [{"sha256": "old"}]}]}
        updated = copy.deepcopy(context)
        updated["revision"] = "b"
        updated["components"][0]["evidence"][0].update(line=9, sha256="new")
        updated["relations"][0]["evidence"][0]["sha256"] = "new"
        self.assertEqual(blueprint.definition_hash(context), blueprint.definition_hash(updated))
        updated["relations"][0]["kind"] = "owns"
        self.assertNotEqual(blueprint.definition_hash(context), blueprint.definition_hash(updated))

    def test_serves_only_accepted_unchanged_artifacts_and_evidence(self):
        # Synthetic renderer/observer HTTP contract; no natural adoption claim.
        fixture_root = Path(__file__).parent / ".archctx"
        fixture_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=fixture_root) as temporary:
            repo = Path(temporary)
            source = "def serve():\n    return '<script>not executable</script>'\n"
            (repo / "service.py").write_text(source, encoding="utf-8")
            config = repo / "architecture.json"
            config.write_text(json.dumps({"version": 1, "repo": ".", "components": [{"id": "service", "evidence": [{"path": "service.py", "contains": "def serve"}]}]}), encoding="utf-8")
            directory = repo / ".archctx"
            generation_id = "a" * 16 + "-" + "b" * 32
            generation = directory / "generations" / generation_id
            generation.mkdir(parents=True)
            output = generation / "current.html"
            output.write_text("<svg></svg>", encoding="utf-8")
            ir = generation / "architecture.archify.json"
            ir.write_text("{}", encoding="utf-8")
            receipt = {"generation": str(generation), "ir": str(ir), "ir_sha256": archctx.sha(ir.read_bytes()),
                       "artifacts": {"current.html": archctx.sha(output.read_bytes())}}
            value = {"status": "FRESH", "last_good_context_hash": "accepted-context", "archify": receipt,
                     "context": {"components": [{"id": "service", "evidence": [{"path": "service.py", "line": 1, "sha256": archctx.sha(source.encode())}]}]}}
            record = {"repo": str(repo), "context": value["context"], "context_hash": "accepted-context", "archify": receipt}
            development = {"status": "FRESH", "stale": False, "accepted_context_hash": "accepted-context",
                           "observation_id": "observation-1", "observer_running": True, "updates": {"status": "FRESH"},
                           "changes": [{"path": "service.py", "sha256": archctx.sha((repo / "service.py").read_bytes())},
                                       {"path": "private.txt", "sha256": None, "observation": "metadata_only"}]}
            (repo / "private.txt").write_text("synthetic private bytes", encoding="utf-8")
            observer = SimpleNamespace(bundle=lambda: (copy.deepcopy(development), copy.deepcopy(record)))
            real_snapshot = archctx.snapshot
            with patch.object(archctx, "snapshot", return_value=value) as snapshot_mock:
                server = ThreadingHTTPServer(("127.0.0.1", 0), blueprint.handler(config, None, observer))
                worker = threading.Thread(target=server.serve_forever, daemon=True)
                worker.start()
                try:
                    response_headers = {}

                    def get(path, host=None, headers=None):
                        connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                        connection.request("GET", path, headers=({"Host": host} if host else {}) | (headers or {}))
                        response = connection.getresponse()
                        result = response.status, response.read().decode()
                        response_headers.clear()
                        response_headers.update(dict(response.getheaders()))
                        connection.close()
                        return result
                    code, body = get("/api/current")
                    self.assertEqual(code, 200)
                    current = json.loads(body)
                    self.assertEqual(current["context_hash"], current["development"]["accepted_context_hash"])
                    self.assertEqual(current["components"], record["context"]["components"])
                    self.assertEqual(snapshot_mock.call_count, 0)  # One paired observer read, not a competing snapshot.
                    etag = response_headers["ETag"]
                    self.assertEqual(get("/api/current", headers={"If-None-Match": etag}), (304, ""))
                    development["observation_id"] = "observation-2"
                    self.assertEqual(get("/api/current", headers={"If-None-Match": etag})[0], 200)
                    self.assertNotEqual(response_headers["ETag"], etag)
                    etag = response_headers["ETag"]
                    development["observer_running"] = False
                    self.assertEqual(get("/api/current", headers={"If-None-Match": etag})[0], 200)
                    self.assertNotEqual(response_headers["ETag"], etag)
                    development["observer_running"] = True
                    self.assertEqual(get("/api/current", "evil.example")[0], 403)
                    self.assertEqual(get(f"/artifact/{generation_id}/current.html"), (200, "<svg></svg>"))
                    original = output.read_bytes()
                    code, mapped = get(f"/map/{generation_id}/current.html")
                    self.assertEqual((code, mapped), (200, original.decode() + blueprint.MAP_BRIDGE))
                    self.assertEqual(output.read_bytes(), original)
                    self.assertEqual(archctx.sha(original), receipt["artifacts"]["current.html"])
                    self.assertIn('sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"', get("/")[1])
                    self.assertNotIn("allow-same-origin", blueprint.PAGE)
                    self.assertIn("e.source!==parent", blueprint.MAP_BRIDGE)
                    self.assertEqual(get(f"/map/{generation_id}/comparison.html")[0], 409)
                    artifact_path = blueprint.artifact_path

                    def modify_after_path_check(*args):
                        path = artifact_path(*args)
                        path.write_text("synthetic concurrent artifact replacement", encoding="utf-8")
                        return path

                    with patch.object(blueprint, "artifact_path", side_effect=modify_after_path_check):
                        self.assertEqual(get(f"/map/{generation_id}/current.html")[0], 409)
                    output.write_bytes(original)
                    self.assertEqual(get("/artifact/older/current.html")[0], 409)
                    self.assertEqual(get(f"/artifact/{generation_id}/secrets.json")[0], 409)
                    code, text = get("/source?component=service&item=0&context=accepted-context")
                    self.assertEqual(code, 200)
                    self.assertIn("&lt;script&gt;", text)
                    self.assertNotIn("<script>", text)
                    working = "/working-source?path=service.py&observation=observation-2"
                    code, text = get(working)
                    self.assertEqual(code, 200)
                    self.assertIn("not accepted evidence", text)
                    self.assertIn("&lt;script&gt;", text)
                    self.assertNotIn("<script>", text)
                    for query in ("path=service.py&observation=observation-1", "path=private.txt&observation=observation-2",
                                  "path=../private.txt&observation=observation-2", "path=/private.txt&observation=observation-2"):
                        code, body = get("/working-source?" + query)
                        self.assertEqual(code, 409)
                        self.assertNotIn("synthetic private bytes", body)
                    (repo / "service.py").write_text("changed", encoding="utf-8")
                    self.assertEqual(get(working)[0], 409)
                    self.assertEqual(get("/source?component=service&item=0&context=accepted-context")[0], 409)
                    output.write_text("modified", encoding="utf-8")
                    self.assertEqual(get(f"/artifact/{generation_id}/current.html")[0], 409)
                    output.write_text("<svg></svg>", encoding="utf-8")
                    archctx.atomic(archctx.last_path(directory), record)
                    config.write_text("{ unfinished edit", encoding="utf-8")
                    development.update(status="INVALID", stale=True, error="synthetic invalid configuration",
                                       observation_id="observation-invalid", observer_running=False,
                                       updates={"status": "INVALID", "freshness": "stale"})
                    snapshot_mock.side_effect = real_snapshot
                    code, body = get("/api/current")
                    current = json.loads(body)
                    self.assertEqual((code, current["status"]), (200, "INVALID"))
                    self.assertEqual(current["context_hash"], "accepted-context")
                    self.assertEqual(current["components"], record["context"]["components"])
                    self.assertEqual(get(f"/artifact/{generation_id}/current.html"), (200, "<svg></svg>"))
                    self.assertEqual(get(f"/map/{generation_id}/current.html")[0], 200)
                    self.assertEqual(get(working)[0], 409)
                    self.assertEqual(get("/source?component=service&item=0&context=accepted-context")[0], 409)
                    self.assertTrue(blueprint.handler(config, None))  # Restart also works during the invalid edit.
                finally:
                    server.shutdown()
                    worker.join()
                    server.server_close()

    def test_artifact_cannot_escape_generation_store(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            private = repo / "secret.html"
            private.write_text("private", encoding="utf-8")
            receipt = {"generation": str(repo), "artifacts": {"secret.html": archctx.sha(private.read_bytes())}}
            with self.assertRaises(ValueError):
                blueprint.artifact_path(repo, repo / ".archctx", receipt, "secret.html")


if __name__ == "__main__":
    unittest.main()
