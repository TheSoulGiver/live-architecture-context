"""Synthetic scope/UI/HTTP contracts; not claims of natural adoption."""
import http.client
import json
import shutil
import subprocess
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import archctx
import archctx_blueprint as blueprint
import archctx_understand as understand
from archctx_development import DevelopmentObserver


class UnderstandPageTest(unittest.TestCase):
    def test_optional_alias_movement_does_not_fail_canonical_observation(self):
        repo, config, state, _ = self.fixture()
        self.receipt(repo, state)
        observer = DevelopmentObserver(config, str(state))
        first = observer.poll_once()
        original = archctx.repo_file
        def moved(root, relative, label):
            if root == repo and relative == "analysis_only.py":
                return repo.parent / "outside.py", relative
            return original(root, relative, label)
        with patch.object(archctx, "repo_file", side_effect=moved):
            changed = observer.poll_once()
        self.assertNotEqual(changed["status"], "INVALID")
        self.assertEqual(changed["updates"]["source_analysis"]["status"], "INVALID")
        self.assertEqual(changed["accepted_context_hash"], first["accepted_context_hash"])
        self.assertEqual(observer.poll_once()["updates"]["source_analysis"]["status"], "FRESH")

    def fixture(self):
        root = Path(__file__).parent / ".archctx"
        root.mkdir(exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(temporary.cleanup)
        repo = Path(temporary.name).resolve()
        config, state = repo / "architecture.json", repo / "state"
        (repo / ".gitignore").write_text("state/\n")
        (repo / "owner.py").write_text("OWNER = 1\n")
        (repo / "analysis_only.py").write_text("VALUE = '<script>not executed</script>'\n")
        archctx.atomic(config, {"version": 1, "repo": ".", "components": [
            {"id": "owner", "purpose": "Synthetic owner", "evidence": [{"path": "owner.py", "contains": "OWNER"}]}]})
        def git(*args):
            subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
        git("init", "-q")
        git("config", "user.email", "fixture@example.invalid")
        git("config", "user.name", "Synthetic Fixture")
        git("add", ".gitignore", "architecture.json", "owner.py", "analysis_only.py")
        git("commit", "-qm", "fixture")
        self.assertEqual(archctx.refresh(config, str(state))["status"], "PASS")
        return repo, config, state, git

    def receipt(self, repo, state, ident="a" * 64, path="analysis_only.py"):
        raw = (repo / path).read_bytes()
        source = state / "understand/runs" / ident / "source"
        archctx.atomic_bytes(source / path, raw)
        value = {"analysis_id": ident, "worktree": str(repo), "source_root": str(source),
                 "source_revision": archctx.revision(repo), "source_hashes": {path: archctx.sha(raw)},
                 "graph_sha256": "b" * 64, "node_count": 1, "edge_count": 0, "tour": [],
                 "provider": {"name": "understand-anything", "url": understand.PROVIDER_URL, "revision": understand.PROVIDER_REVISION},
                 "candidates": [{"id": "ua:" + ident, "title": "Synthetic investigation", "summary": "Not a canonical owner",
                                 "files": [path], "evidence": [{"path": path, "line": 1, "sha256": archctx.sha(raw)}],
                                 "raw_relations": [], "blocking": False}]}
        archctx.atomic(understand.current_path(state), value)
        return value

    def catalog(self, state, *receipts):
        entries = {understand.scope_id(receipt): understand.retain_analysis(state, receipt, b"{}") for receipt in receipts}
        archctx.atomic(state / "understand/catalog.json", {"version": 1,
                       "active": understand.scope_id(receipts[-1]), "scopes": entries})

    def test_observer_tracks_receipt_and_committed_analysis_only_sources_without_idle_reads(self):
        repo, config, state, git = self.fixture()
        observer = DevelopmentObserver(config, str(state))
        first = observer.poll_once()
        self.assertEqual(first["analysis_stat_files"], 0)
        with patch.object(understand, "local_json", side_effect=AssertionError("absent analysis must not be read")):
            self.assertEqual(observer.poll_once()["observation_id"], first["observation_id"])
        self.receipt(repo, state)
        loaded = observer.poll_once()
        self.assertEqual(loaded["updates"]["source_analysis"]["status"], "FRESH")
        self.assertEqual(loaded["analysis_stat_files"], 1)
        count = loaded["metrics"]["full_reads"]
        with patch.object(understand, "local_json", side_effect=AssertionError("idle receipt reread")), patch.object(understand, "verify_sources", side_effect=AssertionError("idle source read")):
            self.assertEqual(observer.poll_once()["metrics"]["full_reads"], count)
        (repo / "analysis_only.py").write_text("VALUE = 'new saved implementation'\n")
        git("add", "analysis_only.py")
        git("commit", "-qm", "saved source change without Git dirtiness")
        changed = observer.poll_once()
        self.assertEqual(changed["updates"]["source_analysis"]["status"], "STALE")
        item = next(c for c in changed["changes"] if c["path"] == "analysis_only.py")
        self.assertTrue(item["analysis_changed"])
        self.assertFalse(item["covered"])
        self.assertEqual(observer._refresh_paths, [])
        self.assertEqual(changed["accepted_context_hash"], first["accepted_context_hash"])
        self.receipt(repo, state, "c" * 64)
        replaced = observer.poll_once()
        self.assertEqual(replaced["updates"]["source_analysis"]["analysis_id"], "c" * 64)
        self.assertEqual(replaced["updates"]["source_analysis"]["status"], "FRESH")
        understand.current_path(state).write_text("{ invalid receipt")
        invalid = observer.poll_once()
        self.assertEqual(invalid["updates"]["source_analysis"]["status"], "INVALID")
        self.assertNotEqual(invalid["status"], "INVALID")
        self.assertEqual(invalid["analysis_stat_files"], 0)

    def test_analysis_source_http_is_content_bound_local_and_escaped(self):
        repo, config, state, _ = self.fixture()
        receipt = self.receipt(repo, state)
        path = "analysis_only.py"
        query = f"/analysis-source?analysis={receipt['analysis_id']}&path={path}&sha={receipt['source_hashes'][path]}&line=1"
        server = ThreadingHTTPServer(("127.0.0.1", 0), blueprint.handler(config, str(state)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def get(url, host=None):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", url, headers={"Host": host} if host else {})
                response = connection.getresponse()
                result = response.status, response.read().decode()
                connection.close()
                return result
            status, body = get(query)
            self.assertEqual(status, 200)
            self.assertIn("Historical captured analysis source", body)
            self.assertIn("not accepted architecture evidence", body)
            self.assertIn("&lt;script&gt;", body)
            self.assertNotIn("<script>", body)
            self.assertEqual(get(query, "evil.invalid")[0], 403)
            for invalid in (query.replace("a" * 64, "c" * 64), query.replace("path=" + path, "path=../owner.py"),
                            query.replace("path=" + path, "path=/owner.py"), query.replace("line=1", "line=-1"),
                            query.replace(receipt["source_hashes"][path], "d" * 64)):
                self.assertEqual(get(invalid)[0], 409)
            # Live source may have moved; this endpoint remains a labelled historical snapshot.
            (repo / path).write_text("current product source differs")
            self.assertEqual(get(query)[0], 200)
            original = understand.analysis_receipt
            reads = []
            def moved(*args):
                receipt_path, value = original(*args)
                reads.append(1)
                if len(reads) > 1:
                    value = {**value, "analysis_id": "e" * 64}
                return receipt_path, value
            with patch.object(understand, "analysis_receipt", side_effect=moved):
                self.assertEqual(get(query)[0], 409)
            forged = {**receipt, "source_root": str(repo)}
            archctx.atomic(understand.current_path(state), forged)
            self.assertEqual(get(query)[0], 409)
            archctx.atomic(understand.current_path(state), receipt)
            (Path(receipt["source_root"]) / path).write_text("mutated captured content")
            self.assertEqual(get(query)[0], 409)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()

    def test_retained_scope_selection_history_and_observer_are_independent_and_read_only(self):
        repo, config, state, git = self.fixture()
        a = self.receipt(repo, state)
        b = self.receipt(repo, state, "c" * 64, "owner.py")
        self.catalog(state, a, b)
        observer = DevelopmentObserver(config, str(state))
        before = observer.poll_once()
        self.assertEqual(before["analysis_stat_files"], 2)
        with patch.object(understand, "local_json", side_effect=AssertionError("idle catalog reread")), \
             patch.object(understand, "verify_sources", side_effect=AssertionError("idle source reread")):
            self.assertEqual(observer.poll_once()["observation_id"], before["observation_id"])
        (repo / "analysis_only.py").write_text("VALUE = 'changed after analysis A'\n")
        git("add", "analysis_only.py")
        git("commit", "-qm", "change retained non-focused scope")
        changed = observer.poll_once()
        packet = changed["updates"]["source_analysis"]
        self.assertEqual(packet["analysis_id"], b["analysis_id"])
        self.assertEqual(packet["status"], "FRESH")
        self.assertEqual({scope["analysis_id"]: scope["status"] for scope in packet["scopes"]},
                         {a["analysis_id"]: "STALE", b["analysis_id"]: "FRESH"})
        self.assertTrue(next(item for item in changed["changes"] if item["path"] == "analysis_only.py")["analysis_changed"])
        self.assertEqual(changed["accepted_context_hash"], before["accepted_context_hash"])
        state_before = {p.relative_to(state): p.read_bytes() for p in state.rglob("*") if p.is_file()}
        server = ThreadingHTTPServer(("127.0.0.1", 0), blueprint.handler(config, str(state), observer))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def get(url, etag=None):
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port)
                connection.request("GET", url, headers={"If-None-Match": etag} if etag else {})
                response = connection.getresponse()
                value = response.status, response.read().decode(), response.getheader("ETag")
                connection.close()
                return value
            with patch.object(understand, "run_upstream", side_effect=AssertionError("GET invokes no provider")), \
                 patch.object(archctx, "atomic", side_effect=AssertionError("GET writes no state")), \
                 patch.object(archctx, "snapshot", side_effect=AssertionError("GET uses paired observer context")):
                code, raw, etag_a = get("/api/current?analysis=" + a["analysis_id"])
                selected = json.loads(raw)
                self.assertEqual((code, selected["development"]["updates"]["source_analysis"]["status"]), (200, "STALE"))
                self.assertEqual(selected["context_hash"], before["accepted_context_hash"])
                self.assertEqual(get("/api/current?analysis=" + a["analysis_id"], etag_a)[:2], (304, ""))
                code, raw, etag_b = get("/api/current?analysis=" + b["analysis_id"], etag_a)
                self.assertEqual((code, json.loads(raw)["development"]["updates"]["source_analysis"]["status"]), (200, "FRESH"))
                self.assertNotEqual(etag_a, etag_b)
                self.assertEqual(observer.read()["updates"]["source_analysis"]["analysis_id"], b["analysis_id"])
                _, raw, _ = get("/api/current?file=never-understood.py")
                unknown = json.loads(raw)["development"]["updates"]["source_analysis"]
                self.assertEqual(unknown["uncovered_files"], ["never-understood.py"])
                self.assertEqual(unknown["candidates"], [])
                self.assertNotEqual(unknown["status"], "FRESH")
                query = "/analysis-source?analysis=" + a["analysis_id"] + "&path=analysis_only.py&sha=" + a["source_hashes"]["analysis_only.py"]
                code, source, _ = get(query)
                self.assertEqual(code, 200)
                self.assertIn("Historical captured analysis source", source)
                self.assertIn("&lt;script&gt;", source)
                self.assertEqual(get(query.replace(a["analysis_id"], "d" * 64))[0], 409)
                self.assertEqual(get("/api/current?analysis=")[0], 409)
                with patch.object(understand, "discoveries", return_value={"configured": True, "accepted_context_hash": "new-accepted"}):
                    self.assertEqual(get("/api/current?analysis=" + a["analysis_id"])[0], 409)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
        self.assertEqual(state_before, {p.relative_to(state): p.read_bytes() for p in state.rglob("*") if p.is_file()})
        receipt_path, _ = understand.analysis_receipt(state, a["analysis_id"])
        receipt_bytes = receipt_path.read_bytes()
        receipt_path.write_bytes(receipt_bytes + b" ")
        damaged = observer.poll_once()
        self.assertEqual(observer._analysis_paths, {"owner.py"})
        self.assertEqual(damaged["accepted_context_hash"], before["accepted_context_hash"])
        self.assertEqual({scope["analysis_id"]: scope["status"] for scope in damaged["updates"]["source_analysis"]["scopes"]},
                         {a["analysis_id"]: "INVALID", b["analysis_id"]: "FRESH"})
        receipt_path.write_bytes(receipt_bytes)
        recovered = observer.poll_once()
        self.assertEqual(recovered["analysis_stat_files"], 2)
        self.assertEqual(recovered["updates"]["source_analysis"]["status"], "FRESH")

    def test_analysis_scope_metadata_is_bounded_and_cannot_escape_repo(self):
        repo, config, state, _ = self.fixture()
        receipt = self.receipt(repo, state)
        observer = DevelopmentObserver(config, str(state))
        for hashes in ({"../outside.py": "a" * 64}, {str(repo / "owner.py"): "a" * 64}, {f"f{i}.py": "a" * 64 for i in range(65)}):
            archctx.atomic(understand.current_path(state), {**receipt, "source_hashes": hashes})
            result = observer._external_state()
            self.assertIn("error", result["analysis_receipt"])
            self.assertEqual(observer._analysis_paths, set())
        understand.current_path(state).write_bytes(b" " * (understand.SUMMARY_BYTES + 1))
        self.assertIn("error", observer._external_state()["analysis_receipt"])

    def test_real_page_script_shows_analysis_without_promoting_it_or_parsing_html(self):
        self.assertLess(blueprint.PAGE.index('id="analysis"'), blueprint.PAGE.index('id="changes"'))
        node = shutil.which("node")
        if not node:
            self.skipTest("Node required for PAGE JavaScript contract")
        source = blueprint.PAGE.split("<script>", 1)[1].split("</script>", 1)[0]
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
class Element{constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.style={};this.textContent='';this.contentWindow={messages:[],postMessage(v){this.messages.push(v)}}}
 append(...v){this.children.push(...v)} replaceChildren(...v){this.children=v} insertBefore(v,b){let i=this.children.indexOf(b);this.children.splice(i<0?this.children.length:i,0,v)}
 setAttribute(k,v){this[k]=v} removeAttribute(k){delete this[k]} querySelector(tag){return this.children.find(v=>v.tag===tag)} cloneNode(){return new Element(this.tag)} remove(){} set innerHTML(v){throw Error('untrusted HTML sink')}}
const elements={},get=id=>elements[id]??=new Element(),text=e=>[e.textContent,...e.children.map(text)].join('\n'),all=e=>[e,...e.children.flatMap(all)],requests=[];let message;
const accepted={status:'FRESH',context_hash:'accepted',generation:'accepted',revision:'product',artifacts:['current.html'],components:[{id:'owner',evidence:[]}],relations:[],development:{observation_id:'one',changes:[],updates:{}}};
let next={status:'MISSING',context_hash:null,generation:'',artifacts:[],components:[],relations:[],development:{observation_id:'setup',changes:[],updates:{}}};
const context=vm.createContext({TextEncoder,document:{getElementById:get,createElement:tag=>new Element(tag)},window:{addEventListener:(_,f)=>message=f},setTimeout(){},clearTimeout(){},
 fetch:async url=>{requests.push(url);return {status:200,ok:true,json:async()=>next,headers:{get:()=>next.development.observation_id}}}});
const read=s=>vm.runInContext(s,context);
(async()=>{vm.runInContext(require('node:fs').readFileSync(0,'utf8'),context);await new Promise(setImmediate);
 assert.equal(read('loading'),null);assert.ok(text(get('analysis')).includes('尚未进行源码理解'));assert.ok(text(get('reason')).includes('没有可用的已接受图'));assert.equal(get('state').textContent,'尚无已展示的已接受图');
 next=JSON.parse(JSON.stringify(next));next.development.observation_id='first-analysis';next.development.updates.source_analysis={configured:true,status:'FRESH',accepted_context_hash:null,candidate_count:1,
 candidates:[{id:'ua:first',title:'First setup discovery',review_state:'unreviewed',related_components:[],evidence:[]}]};
 await context.poll();assert.ok(text(get('analysis')).includes('First setup discovery'));assert.ok(text(get('analysis')).includes('已发现 · 待复审'));assert.ok(text(get('analysis')).includes('尚未确认归属'));
 assert.equal(read('data.context_hash'),null);assert.equal(read('data.components.length'),0);assert.equal(read('data.relations.length'),0);assert.equal(read('loading'),null);assert.ok(text(get('reason')).includes('没有可用的已接受图'));
 next.development.observation_id='first-analysis-stale';next.development.updates.source_analysis.status='STALE';next.development.updates.source_analysis.changed_files=['scope.py'];await context.poll();
 assert.ok(text(get('analysis')).includes('STALE'));assert.ok(text(get('analysis')).includes('分析后已变化：scope.py'));
 next.development.observation_id='first-analysis-invalid';next.development.updates.source_analysis={configured:true,status:'INVALID',reason:'bad first receipt',candidates:[]};await context.poll();
 assert.ok(text(get('analysis')).includes('bad first receipt'));assert.equal(text(get('analysis')).includes('First setup discovery'),false);assert.equal(read('loading'),null);assert.equal(read('data.context_hash'),null);
 next=accepted;await context.poll();message({source:read('loading').contentWindow,data:{kind:'lac-ready'}});
 assert.equal(get('analysis').hidden,false);assert.ok(text(get('analysis')).includes('尚未进行源码理解'));const frame=read('picture');
 next=JSON.parse(JSON.stringify(next));next.development.observation_id='two';next.development.updates.source_analysis={configured:true,status:'FRESH',analysis_id:'a'.repeat(64),provider:{name:'understand-anything',revision:'pin'},source_files:['scope.py'],
 candidates:[{id:'ua:fixture',content_revision:'content-1',evidence_revision:'evidence-1',title:'<img src=x onerror=bad()>',summary:'<script>bad()</script>',review_state:'unreviewed',related_components:['owner'],evidence:[{path:'scope.py',line:2,sha256:'b'.repeat(64)}],raw_relations:[{source:'file:a',target:'file:b',type:'imports',direction:'backward'}]}],
 tour:Array.from({length:7},(_,i)=>({order:i+1,title:'step-'+i,description:'scoped reading'}))};
 await context.poll();assert.equal(read('picture'),frame);assert.equal(read('data.components.length'),1);assert.equal(get('analysis').hidden,false);
 const body=text(get('analysis'));for(const expected of ['已发现 · 待复审','尚未确认归属','不是 canonical 组件','<img src=x onerror=bad()>','<script>bad()</script>','owner','imports','direction=backward'])assert.ok(body.includes(expected),expected);
 const links=e=>e.children.flatMap(v=>[...(v.tag==='a'?[v]:[]),...links(v)]);assert.ok(links(get('analysis'))[0].href.includes('/analysis-source?analysis='+'a'.repeat(64)));
 assert.deepEqual(Array.from(frame.contentWindow.messages.at(-1).direct),[]);
 assert.equal(context.findingState({status:'STALE',files:['scope.py']},{status:'STALE',changed_files:['dependency.py']})[0],'源码已变化 · 待重新理解');
 next.development.updates.source_analysis.candidates[0].previous_review={decision:'accepted',at:'historical-input'};await context.poll();
 assert.ok(text(get('analysis')).includes('历史复审：accepted · historical-input；不代表当前确认。'));assert.ok(text(get('analysis')).includes('尚未确认归属'));
 assert.equal(read('data.relations.length'),0);get('flow-view').onclick();assert.equal(get('flows').hidden,false);assert.equal(get('analysis').hidden,true);
 for(const expected of ['已发现的源码导览 · 待核对流程','step-6'])assert.ok(text(get('flows')).includes(expected),expected);get('now').onclick();assert.equal(read('picture'),frame);
 next.development.updates.source_analysis.scopes=[
 {scope_id:'scope-a',analysis_id:'a'.repeat(64),source_files:['scope.py'],status:'FRESH',selected:true,candidate_count:1},
 {scope_id:'scope-c',analysis_id:'c'.repeat(64),source_files:['other.py'],status:'STALE',selected:false,candidate_count:1},
 {scope_id:'scope-d',analysis_id:'d'.repeat(64),source_files:['unknown.py'],status:'INVALID',selected:false,candidate_count:0}];
 await context.poll();for(const expected of ['当前查看 · scope.py · 源码一致','other.py · 源码已变化','unknown.py · 状态未知'])assert.ok(text(get('analysis')).includes(expected),expected);
 const chooser=all(get('analysis')).find(e=>e.tag==='select');assert.equal(chooser['aria-label'],'源码理解范围');
 assert.ok(chooser.children.some(e=>e.value==='scope-c'));chooser.value='scope-c';next=JSON.parse(JSON.stringify(next));Object.assign(next.development.updates.source_analysis,{analysis_id:'c'.repeat(64),status:'STALE',source_files:['other.py']});
 next.development.updates.source_analysis.candidates[0].analysis_id='c'.repeat(64);chooser.onchange();await new Promise(setImmediate);
 assert.equal(requests.at(-1),'/api/current?analysis=scope-c');assert.equal(read('picture'),frame);assert.equal(read('data.context_hash'),'accepted');
 assert.ok(links(get('analysis'))[0].href.includes('/analysis-source?analysis='+'c'.repeat(64)));assert.equal(read('data.components.length'),1);
 const retained=next.development.updates.source_analysis;next.development.updates.source_analysis={configured:true,status:'UNKNOWN',scopes:retained.scopes,uncovered_files:['missing.py'],candidates:[]};
 read("analysisQuery='?file=missing.py'");await context.poll();assert.ok(text(get('analysis')).includes('请求未覆盖：missing.py'));assert.ok(text(get('analysis')).includes('当前请求没有可复用的理解范围'));assert.ok(all(get('analysis')).some(e=>e.tag==='select'));
 assert.equal(links(get('analysis')).length,0);assert.equal(read('picture'),frame);next.development.updates.source_analysis=retained;read("analysisQuery=''");await context.poll();
 next.development.observation_id='three';next.development.updates.source_analysis.status='STALE';next.development.updates.source_analysis.changed_files=['scope.py'];await context.poll();assert.ok(text(get('analysis')).includes('STALE'));assert.ok(text(get('analysis')).includes('分析后已变化：scope.py'));assert.equal(read('data.context_hash'),'accepted');
 next.development.observation_id='four';next.development.updates.source_analysis={configured:true,status:'INVALID',reason:'bad receipt',candidates:[]};await context.poll();assert.ok(text(get('analysis')).includes('INVALID'));assert.equal(read('picture'),frame);
 next.development.updates.source_analysis={configured:false};await context.poll();assert.equal(get('analysis').hidden,false);assert.ok(text(get('analysis')).includes('尚未进行源码理解'));assert.equal(read('data.components.length'),1);assert.equal(read('data.context_hash'),'accepted');console.log('PASS: native analysis empty state, separate discovery/tour, text-only content and accepted map/navigation preserved');
})().catch(e=>{console.error(e);process.exitCode=1});
"""
        result = subprocess.run([node, "-e", harness], input=source, text=True, encoding="utf-8", capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
