#!/usr/bin/env python3
"""Render accepted architecture with Archify and serve its local, live view."""
from __future__ import annotations

import argparse
import html
import json
import mimetypes
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import archctx
from archctx_development import DevelopmentObserver

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


def definition_hash(context: dict[str, Any]) -> str:
    return archctx.semantic({
        "components": sorted((archctx.semantic_component(x) for x in context["components"]), key=lambda x: x["id"]),
        "relations": sorted((archctx.semantic_relation(x) for x in context.get("relations", [])), key=archctx.relation_id),
        "coverage": context.get("coverage"),
    })


def artifact_path(repo: Path, directory: Path, receipt: dict[str, Any], name: str) -> Path:
    generation = Path(receipt["generation"])
    if not generation.is_absolute():
        generation = repo / generation
    generation = generation.resolve()
    generation.relative_to((directory / "generations").resolve())
    if name != Path(name).name or name not in receipt.get("artifacts", {}):
        raise ValueError("unpublished blueprint artifact")
    path = (generation / name).resolve()
    path.relative_to(generation)
    if path.stat().st_size > MAX_ARTIFACT_BYTES:
        raise ValueError("blueprint artifact exceeds local size limit")
    if archctx.sha(path.read_bytes()) != receipt["artifacts"][name]:
        raise ValueError("blueprint artifact changed after acceptance")
    return path


def render_bundle(config: dict[str, Any], repo: Path, directory: Path, generation: Path,
                  ir: Path, candidate: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """Prepare artifacts only. The refresh transaction alone publishes their receipt."""
    settings = config["archify"]
    current, comparison, receipt = generation / "current.html", generation / "comparison.html", generation / "compare.json"
    prior = previous.get("archify", {}) if previous else {}
    before = ir
    before_reason = "no earlier accepted render"
    if prior.get("generation") and prior.get("ir"):
        try:
            path = Path(prior["ir"])
            if not path.is_absolute():
                path = repo / path
            path.resolve().relative_to((directory / "generations").resolve())
            if archctx.sha(path.read_bytes()) != prior.get("ir_sha256"):
                raise ValueError("previous accepted IR changed")
            before, before_reason = path, None
        except (OSError, ValueError):
            before_reason = "previous accepted IR unavailable; comparison is a current-only baseline"
    values = {"{repo}": str(repo), "{state}": str(directory), "{archify_output}": str(ir),
              "{archify_html}": str(current), "{archify_before}": str(before),
              "{archify_compare}": str(comparison), "{archify_receipt}": str(receipt)}
    delivered = None
    for operation, required in (("render", ("{archify_output}", "{archify_html}")),
                                ("compare", ("{archify_before}", "{archify_output}", "{archify_compare}", "{archify_receipt}"))):
        command = settings.get(operation)
        if not isinstance(command, list) or not all(token in command for token in required):
            raise ValueError(f"archify.{operation} must include its input and output placeholders")
        _, result = archctx.run(command, repo, settings.get("timeout_seconds", 180), values)
        if result.returncode:
            raise RuntimeError(f"Archify {operation} failed ({result.returncode}): {(result.stderr or result.stdout)[-1000:]}")
        if operation == "render":
            delivered = json.loads(result.stdout)
    for path in (current, comparison):
        if not path.is_file() or path.stat().st_size > MAX_ARTIFACT_BYTES or "<svg" not in path.read_text(encoding="utf-8").lower():
            raise ValueError(f"Archify did not produce a bounded SVG-backed {path.name}")
    if (not isinstance(delivered, dict) or delivered.get("ok") is not True
            or delivered.get("specification", {}).get("sha256") != archctx.sha(ir.read_bytes())
            or delivered.get("artifact", {}).get("sha256") != archctx.sha(current.read_bytes())):
        raise ValueError("Archify delivery receipt does not bind the accepted IR and rendered artifact")
    compared = archctx.load(receipt)
    if (compared.get("ok") is not True or compared.get("base", {}).get("rawSha256") != archctx.sha(before.read_bytes())
            or compared.get("head", {}).get("rawSha256") != archctx.sha(ir.read_bytes())):
        raise ValueError("Archify comparison receipt does not bind the accepted before/after IR")
    if compared.get("artifact", {}).get("sha256") != archctx.sha(comparison.read_bytes()):
        raise ValueError("Archify comparison receipt does not bind its rendered artifact")
    validation = compared.get("validation", {})
    if not validation.get("checkCount") or validation.get("checksPassed") != validation["checkCount"]:
        raise ValueError("Archify comparison validation did not pass")
    definition = definition_hash(candidate)
    view_file, _ = archctx.repo_file(repo, settings["view"], "archify.view")
    view_hash = archctx.semantic(archctx.load(view_file))
    kind = "initial"
    if previous:
        if definition != definition_hash(previous["context"]):
            kind = "architecture"
        elif prior.get("view_hash") != view_hash:
            kind = "presentation"
        elif previous.get("context_hash") != archctx.semantic(candidate):
            kind = "source_evidence"
        else:
            kind = "unchanged"
    binding = {"context_hash": archctx.semantic(candidate), "revision": candidate["revision"],
               "definition_hash": definition, "view_hash": view_hash, "delta_kind": kind,
               "previous_context_hash": previous.get("context_hash") if previous else None,
               "before_available": before != ir, "before_reason": before_reason, "ir_sha256": archctx.sha(ir.read_bytes()),
               "artifacts": {p.name: archctx.sha(p.read_bytes()) for p in (current, comparison, receipt)},
               "comparison": {key: compared[key] for key in ("proofLevel", "completeness", "validation") if key in compared}}
    archctx.atomic(generation / "binding.json", binding)
    if sum(p.stat().st_size for p in generation.iterdir() if p.is_file()) > MAX_ARTIFACT_BYTES:
        raise ValueError("Archify generation exceeds the 8 MiB local bundle limit")
    return {**binding, "render": "PASS", "compare": "PASS",
            "artifacts": {**binding["artifacts"], "binding.json": archctx.sha((generation / "binding.json").read_bytes())}}


PAGE = r"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Live Development Map · LAC</title><style>
:root{--ink:#203a37;--muted:#637773;--line:#d6dfd6;--paper:#f5f5ed;--green:#176958;--amber:#a95412;--red:#ae3e38}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:14px "Segoe UI","Microsoft YaHei",sans-serif}
header{height:102px;display:flex;align-items:center;justify-content:space-between;padding:18px 28px;border-bottom:1px solid var(--line);gap:20px}
.eyebrow{font:11px Consolas,monospace;letter-spacing:2px;color:var(--green)}h1{font:29px Georgia,"Microsoft YaHei",serif;letter-spacing:-.5px;margin:5px 0}h2{font-size:17px;margin:0 0 12px}h3{font-size:14px;margin:19px 0 8px}
small,.muted{color:var(--muted)}p{line-height:1.65;margin:8px 0}code{font:12px Consolas,monospace;overflow-wrap:anywhere}a{color:var(--green);text-decoration-thickness:1px;text-underline-offset:3px}
button,select{font:inherit;color:inherit;background:transparent;border:1px solid var(--line);border-radius:5px;padding:8px 12px;cursor:pointer}button:hover,button[aria-pressed=true]{background:#e2eadd;border-color:#648976}button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #d8942b;outline-offset:3px}
.status{font-size:12px;display:flex;align-items:center;gap:8px;justify-content:flex-end}.dot{width:8px;height:8px;border-radius:50%;background:var(--green)}.stale .dot{background:var(--amber)}#identity{display:block;font:11px Consolas,monospace;max-width:360px;margin-top:8px;text-align:right}
nav{height:54px;display:flex;align-items:center;gap:7px;padding:8px 24px;background:#fffdf7;border-bottom:1px solid var(--line)}nav .spacer{flex:1}nav a{font-size:12px}
main{display:grid;grid-template-columns:minmax(0,1fr) 330px;height:calc(100vh - 190px);min-height:430px}.canvas{min-width:0;position:relative;display:flex;flex-direction:column;background:#fff;border-right:1px solid var(--line)}
.canvas-head{padding:16px 24px 10px;display:flex;justify-content:space-between;gap:12px}.canvas-head h2{margin:0 0 5px}.legend{font-size:11px;display:flex;gap:12px;flex-wrap:wrap;margin-top:8px}.legend span:before{content:'';display:inline-block;border:2px solid #6b8f80;width:10px;height:10px;margin-right:5px}.legend .dirty:before{border-color:#b66a13}.legend .pending:before{border-style:dashed;border-color:#b44e75}.legend .indirect:before{border-color:#9099a9}
#stage{position:relative;flex:1;min-height:0}iframe{width:100%;height:100%;border:0;background:#fff}.map-controls{display:flex;gap:5px;align-items:center;align-self:flex-start}.map-controls button{padding:5px 10px;font-size:12px}
#empty{position:absolute;inset:25% 15%;text-align:center;color:var(--muted);pointer-events:none}#empty[hidden]{display:none}#reason{padding:9px 24px;background:#fff3dd;color:#794917;font-size:12px;line-height:1.5;border-top:1px solid #ecd4aa;max-height:86px;overflow:auto}#reason:empty{display:none}
aside{padding:20px;overflow:auto;background:#fafbf6}#summary{font-size:12px;line-height:1.65;color:var(--muted);border-bottom:1px solid var(--line);padding-bottom:15px;margin-bottom:18px}.section-label{font-size:11px;letter-spacing:1px;color:var(--muted);margin-bottom:8px}
.change{border-left:3px solid #c27c27;padding:9px 10px;background:#fff8e9;margin:8px 0;font-size:12px}.change.unmapped{border-left-style:dashed;border-color:#99a4a1;background:#eff1ec}.change.pending{border-color:#ad5575;background:#f8edf2}.change.accepted{border-color:var(--green);background:#eaf3ea}.change button{padding:3px 6px;font-size:11px;margin:6px 4px 0 0}.change code{display:block}
.tag{display:inline-block;font-size:10px;color:var(--muted);border:1px solid var(--line);padding:2px 5px;border-radius:3px;margin:3px 4px 3px 0}.node-button{display:block;width:100%;text-align:left;padding:10px 8px;border-width:0 0 1px;border-radius:0}.node-button small{display:block;margin-top:4px;font-size:11px}
#detail{margin-top:12px;padding-top:16px;border-top:1px solid var(--line)}#detail:empty{display:none}#detail .name{font-size:16px;font-weight:600}details{margin:12px 0}summary{cursor:pointer;font-size:12px;font-weight:600}details li{margin:7px 0;font-size:12px;line-height:1.55}ul{padding-left:18px}.source{display:block;margin:8px 0;font:11px Consolas,monospace;overflow-wrap:anywhere}
footer{height:34px;border-top:1px solid var(--line);display:flex;justify-content:space-between;gap:12px;padding:9px 24px;font-size:10px;color:var(--muted)}#observation{font-family:Consolas,monospace}
@media(max-width:950px){header{padding:14px 16px}h1{font-size:24px}main{grid-template-columns:minmax(0,1fr) 280px}.canvas-head{padding:12px}aside{padding:14px}.canvas-head small{display:none}}
@media(max-width:680px){header{height:auto;min-height:110px;align-items:flex-start}header small{display:none}#identity{max-width:150px}h1{font-size:21px}nav{padding:7px;gap:3px}nav button{padding:7px;font-size:12px}nav a{display:none}main{height:auto;display:flex;flex-direction:column}.canvas{height:62vh;min-height:400px}aside{max-height:none}footer{height:auto;padding:10px;flex-wrap:wrap}.canvas-head{gap:5px}.map-controls{flex-wrap:wrap}}
</style>
<header><div><div class="eyebrow">LAC / LIVING BLUEPRINT</div><h1>系统在这里，变化也在这里。</h1><small>已接受架构 + 当前开发变化 · 源码始终是最终依据</small></div><div><div class="status" id="status"><i class="dot"></i><span id="state">正在读取已接受蓝图</span></div><code id="identity"></code></div></header>
<nav aria-label="地图视图"><button id="now" aria-pressed="true">当前系统</button><button id="recent" aria-pressed="false">最近变化</button><button id="delta" aria-pressed="false">Before / After</button><span class="spacer"></span><a id="raw" target="_blank" rel="noopener">打开原始 Archify 图 ↗</a></nav>
<main><section class="canvas"><div class="canvas-head"><div><h2 id="map-title">已接受的系统蓝图</h2><small id="map-caption">点击组件，查看职责、上下游和源码。</small><div class="legend"><span>已接受</span><span class="dirty">代码已变</span><span class="pending">语义待确认</span><span class="indirect">关联影响</span></div></div><div class="map-controls"><button id="zoom-out" aria-label="缩小">−</button><button id="zoom-in" aria-label="放大">＋</button><button id="fit">全景</button><button id="focus" aria-pressed="false">聚焦邻居</button></div></div>
<div id="stage"><iframe id="picture" title="Archify 系统图与开发变化" sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"></iframe><div id="empty">等待可用的已接受 Archify 图。<p>局部变化不会被猜成正式架构。</p></div></div><div id="reason" role="status"></div></section>
<aside><div class="section-label" id="panel-title">开发观察 / 非运行状态</div><div id="summary"></div><div id="detail"></div><div id="changes"></div><details id="components-list"><summary>全部组件</summary><div id="sources"></div></details><details id="coverage"><summary>覆盖范围与可信边界</summary><p id="scope"></p><ul id="limits"></ul><p id="graph" class="muted"></p></details></aside></main>
<footer><span id="observation">OBSERVATION —</span><span>文件活动 ≠ 任务进度 ≠ 线上运行 · 本地只读页面</span></footer>
<script>
let data=null,latest=null,mode='now',selected=null,focused=false,version='',navigation=null,lastObservation='',detailVersion='',etag='',loading=null,loadingKey='',loadTimer;
const $=id=>document.getElementById(id),enc=encodeURIComponent;
let picture=$('picture');
const nodeId=id=>'c-'+Array.from(new TextEncoder().encode(id),b=>b.toString(16).padStart(2,'0')).join('');
const el=(tag,text,cls)=>{let e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e};
const unique=items=>[...new Set(items)];
function send(extra={}){if(!data)return;let d=data.development||{},changes=d.changes||[],pending=d.pending||{};
 picture.contentWindow.postMessage({kind:'lac-map',context:data.context_hash,selected:selected?nodeId(selected):null,focused,
 dirty:unique(changes.flatMap(x=>x.components||[])).map(nodeId),indirect:unique(changes.flatMap(x=>x.impacted_components||[])).map(nodeId),
 pending:unique([...(pending.changed_components||[]),...(pending.removed_components||[]),...(pending.added_components||[])]).map(nodeId),
 relations:[...(pending.added_relations||[]),...(pending.removed_relations||[]),...(pending.changed_relations||[])],...extra},'*');}
function select(id){if(!(data?.components||[]).some(c=>c.id===id))return;selected=id;detailVersion='';detail();send();}
function detail(){let key=data?.context_hash+':'+selected;if(key===detailVersion)return;detailVersion=key;let expanded=$('detail').dataset.component===selected?($('detail').querySelector('details')?.open??true):true;$('detail').replaceChildren();$('detail').dataset.component=selected||'';let c=data?.components?.find(c=>c.id===selected);if(!c)return;
 let out=$('detail');out.append(el('div',c.name||c.id,'name'),el('code',c.id),el('p',c.purpose||''));
 for(let t of c.tags||[])out.append(el('span',t,'tag'));
 for(let [title,key,other] of [['上游 / 谁使用它','to','from'],['下游 / 它依赖谁','from','to']]){let relations=(data.relations||[]).filter(r=>r[key]===c.id);out.append(el('h3',title));if(!relations.length)out.append(el('small','当前声明未覆盖此关系'));for(let r of relations){let b=el('button',r[other]+' · '+r.kind,'node-button');b.onclick=()=>select(r[other]);out.append(b);}}
 let d=el('details');d.open=expanded;d.append(el('summary','源码证据 · '+(c.evidence||[]).length+' 个锚点'));
 for(let [i,e] of (c.evidence||[]).entries()){let a=el('a',e.path+':'+e.line,'source');a.target='_blank';a.rel='noopener';a.href='/source?component='+enc(c.id)+'&item='+i+'&context='+enc(data.context_hash);d.append(a)}out.append(d);
}
function card(title,path,owners,cls=''){let c=el('div',undefined,'change '+cls);c.append(el('div',title));if(path)c.append(el('code',path));for(let id of owners||[]){let owner=data.components?.find(x=>x.id===id);if(!owner){c.append(el('span','待确认 · '+id,'tag'));continue}let b=el('button',owner.name||id);b.onclick=()=>select(id);c.append(b)}return c;}
function drawChanges(){let d=data.development||{},p=d.pending||{},box=$('changes');box.replaceChildren();
 if(mode==='recent'){let delta=data.recent_delta||{},changed=delta.changed_components||[];box.append(el('h2','最近接受的变化'));if(data.before_available){box.append(card('已接受 · '+(data.delta_kind||'unknown'),'',changed,'accepted'));for(let [key,label] of [['added_relations','新增关系'],['removed_relations','移除关系'],['changed_relations','关系语义变化']])for(let r of delta[key]||[])box.append(card(label,typeof r==='string'?r:(r.id||r.from+' → '+r.to),[],'accepted'));if(delta.evidence_changed_components?.length)box.append(card('源码证据更新（不等于新增架构）','',delta.evidence_changed_components,'accepted'));}else box.append(el('p',data.before_reason||'没有可用的上一接受版本。'));}
 else {box.append(el('h2','工作区的变化'));let labels={add:'新增文件',modify:'已保存修改',delete:'已删除',rename:'已重命名',untracked:'未跟踪文件'};
 for(let x of d.changes||[]){let c=card((labels[x.kind]||x.kind)+(x.components?.length?'':' · 未覆盖'),x.old_path?x.old_path+' → '+x.path:x.path,x.components,x.components?.length?'':'unmapped');if(x.sha256){let a=el('a','查看当前源码 ↗','source');a.href='/working-source?path='+enc(x.path)+'&observation='+enc(d.observation_id);a.target='_blank';a.rel='noopener';c.append(a)}box.append(c)};
 if(!(d.changes||[]).length)box.append(el('p','当前没有观察到文件变化。','muted'));
 let pendingBox=el('div');
 for(let [key,label] of [['added_components','新增组件待确认'],['removed_components','移除组件待确认'],['changed_components','职责 / 入口待确认']])if(p[key]?.length)pendingBox.append(card(label,'',p[key],'pending'));
 for(let [key,label] of [['added_relations','新增关系待确认'],['removed_relations','移除关系待确认'],['changed_relations','关系变化待确认']])for(let r of p[key]||[])pendingBox.append(card(label,typeof r==='string'?r:(r.id||r.from+' → '+r.to),typeof r==='object'?[r.from,r.to]:[],'pending'));
 for(let c of d.updates?.candidates||[])pendingBox.append(card(c.review_decision?'已判断 · '+c.review_decision+' · 待成功发布':'高价值信号 · Codex 待判断',c.path||c.kind,[],'pending'));box.insertBefore(pendingBox,box.children[1]||null);
 if(d.counts?.omitted)box.append(el('p','另有 '+d.counts.omitted+' 项未展开；观察有界，不代表完整覆盖。','muted'));}
}
const desiredKey=()=>latest?.generation+':'+(mode==='delta'?'comparison.html':'current.html');
function cancelLoad(){clearTimeout(loadTimer);loading?.remove();loading=null;loadingKey='';}
function waiting(message){$('state').textContent='图待更新 · 保留已展示版本';$('status').className='status stale';$('reason').textContent=message+' 最新接受状态 '+(latest?.context_hash||'none').slice(0,12)+'。';}
function view(){let key=desiredKey();if(loading&&loadingKey!==key)cancelLoad();
 if(!latest?.artifacts?.length){if(!data)paint(latest);waiting('没有可用的已接受图。');return;}
 if(key===version){paint(latest);return;}if(data?.generation===latest.generation)paint(latest);
 waiting('新图正在加载；图、组件详情和源码入口将一起切换。');if(key===loadingKey)return;
 loading=picture.cloneNode(false);loading.removeAttribute('src');loading.id='loading-picture';loading.style.cssText='position:absolute;inset:0;visibility:hidden;pointer-events:none';loadingKey=key;$('stage').append(loading);loading.src='/map/'+latest.generation+'/'+(mode==='delta'?'comparison.html':'current.html');
 loadTimer=setTimeout(()=>{cancelLoad();etag='';waiting('新图加载失败，上一可读图及其架构信息保持不变；自动重试。');},8000);}
for(let id of ['now','recent','delta'])$(id).onclick=()=>{mode=id;for(let t of ['now','recent','delta'])$(t).setAttribute('aria-pressed',t===id);view();};
$('focus').onclick=()=>{focused=!focused;$('focus').setAttribute('aria-pressed',focused);send()};
for(let [id,action] of [['zoom-in','in'],['zoom-out','out'],['fit','fit']])$(id).onclick=()=>{if(action==='fit'){focused=false;navigation=null;$('focus').setAttribute('aria-pressed','false')}send({action})};
window.addEventListener('message',e=>{if(!e.data||typeof e.data!=='object')return;
 if(loading&&e.source===loading.contentWindow&&e.data.kind==='lac-ready'){if(loadingKey!==desiredKey()){cancelLoad();return;}clearTimeout(loadTimer);picture.remove();picture=loading;picture.id='picture';picture.style.cssText='';version=loadingKey;loading=null;loadingKey='';$('empty').hidden=true;paint(latest);send({restore:navigation});return;}
 if(e.source!==picture.contentWindow)return;
 if(e.data.kind==='lac-select'){let c=data?.components?.find(c=>nodeId(c.id)===e.data.id);if(c)select(c.id)}
 if(e.data.kind==='lac-navigation'&&e.data.navigation&&typeof e.data.navigation==='object')navigation=e.data.navigation;
});
function paint(next){if(!next)return;data=next;let d=data.development||{},fresh=data.status==='FRESH';
 let comparing=version.endsWith(':comparison.html');for(let id of ['zoom-in','zoom-out','fit','focus'])$(id).disabled=comparing;
 $('map-caption').textContent=comparing?'这里只显示已接受 IR 的差异；使用图内 Before / Delta / After 控件。':'点击组件，查看职责、上下游和源码。';
 if(selected&&!data.components?.some(c=>c.id===selected)){selected=null;focused=false;$('focus').setAttribute('aria-pressed','false');}
 $('map-title').textContent=version.endsWith(':comparison.html')?'已接受版本 · Before / Delta / After':mode==='recent'?'最近变化 · 仍以已接受版本为准':'已接受的系统蓝图';
 if(version)$('raw').href='/artifact/'+version.replace(':','/');
 $('state').textContent=fresh?'已接受 · 源码证据一致':data.status+' · 保留上次蓝图';$('status').className='status'+(fresh?'':' stale');
 $('identity').textContent='CONTEXT '+(data.context_hash||'none').slice(0,12)+' / '+(data.revision||'unknown').slice(0,8);
 $('summary').textContent=(data.components||[]).length+' 个已接受组件 · '+(data.relations||[]).length+' 条声明关系。'+(d.observer_running?(d.live?'持续观察已启动。':'只读观察；未启用自动发布。'):'观察线程未运行；保留最后观察。')+' 代码变化先显示，架构语义由 Codex 维护。';
 let reasons=[...(data.reason||[]),...(d.refresh?.reasons||[])];$('reason').textContent=unique(reasons).join(' · ');
 $('scope').textContent=data.coverage?.scope||'仅覆盖配置声明的组件与证据。';$('limits').replaceChildren(...[...(data.coverage?.limitations||[]),...(d.limitations||[])].map(s=>el('li',s)));
 $('graph').textContent=data.graph?.configured?'代码图：'+(data.graph.provider||'external')+' / '+(data.graph.freshness||'unverified'):'CALM 未接入；图中是架构声明，不是完整调用图。';
 $('observation').textContent='OBS '+(d.observation_id||'—').slice(0,12)+' · '+(d.observed_at||'').replace('T',' ').slice(0,19)+' UTC · '+(d.counts?.unmapped||0)+' 项未覆盖';
 let observation=data.context_hash+':'+d.observation_id;if(observation!==lastObservation){lastObservation=observation;$('sources').replaceChildren(...(data.components||[]).map(c=>{let b=el('button',c.name||c.id,'node-button');b.append(el('small',c.id));b.onclick=()=>select(c.id);return b}));detail();send();}drawChanges();}
async function poll(){try{let r=await fetch('/api/current',{cache:'no-store',headers:etag?{'If-None-Match':etag}:{}});if(r.status===304)return;if(!r.ok)throw Error(await r.text());let next=await r.json();etag=r.headers.get('ETag')||'';
 if(next.status==='RETRY')throw Error('观察期间状态已变化，保留当前画面，下一次读取重试。');latest=next;view();
}catch(e){etag='';$('state').textContent='暂不可更新 · 保留当前画面';$('reason').textContent=String(e)}finally{setTimeout(poll,1500)}}poll();
</script></html>"""

# The original receipt-bound Archify artifact stays untouched. This local-only
# bridge runs inside its existing opaque sandbox and uses Archify's own camera.
MAP_BRIDGE = r"""<style>
html{color-scheme:light}body{background:#fff!important;padding:0!important;margin:0!important;overflow:hidden!important}.container{margin:0!important;padding:0!important;max-width:none!important;width:100%!important}.toolbar,.header,.footer,.share-chapter,.guided-views,.diagram-guide,.overview-map,.route-probe,.semantic-lens,.node-finder,.focus-chip,.diagram-nav{display:none!important}
.diagram-container{margin:0!important;padding:0!important;border:0!important;box-shadow:none!important;border-radius:0!important;height:100vh!important;min-height:0!important;overflow:auto!important;display:flex;align-items:center}
.diagram-container>svg{max-height:100vh!important;width:100%!important;height:100%!important}.lac-dirty>rect:not(.c-mask){stroke:#b66a13!important;stroke-width:3!important}.lac-pending>rect:not(.c-mask){stroke:#b44e75!important;stroke-width:3!important;stroke-dasharray:7 4}.lac-indirect>rect:not(.c-mask){stroke:#8c98a6!important;stroke-width:2!important;stroke-dasharray:3 3}.lac-selected>rect:not(.c-mask){filter:drop-shadow(0 2px 3px #17695855);stroke-width:3!important}.lac-dim{opacity:.22!important}.lac-relation{stroke:#b44e75!important;stroke-width:3!important;stroke-dasharray:6 4}
</style><script>
(()=>{document.documentElement.setAttribute('data-theme','light');let svg=document.querySelector('.diagram-container>svg'),nodes=[...document.querySelectorAll('[data-node-id]')];if(!svg)return;let applying=false,timer;
const emit=(kind,more={})=>parent.postMessage({kind,...more},'*');
function navigation(){if(applying)return;clearTimeout(timer);timer=setTimeout(()=>{let v=Archify.view;emit('lac-navigation',{navigation:{scale:v?.state?.().scale,viewport:v?.logicalViewport?.()}})},80)}
for(let n of nodes){n.addEventListener('click',e=>{e.stopImmediatePropagation();emit('lac-select',{id:n.dataset.nodeId})},true);n.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();e.stopImmediatePropagation();emit('lac-select',{id:n.dataset.nodeId})}},true)}
window.addEventListener('message',e=>{let m=e.data;if(e.source!==parent||m?.kind!=='lac-map')return;let dirty=new Set(m.dirty||[]),pending=new Set(m.pending||[]),indirect=new Set(m.indirect||[]),related=new Set([m.selected]);
for(let p of svg.querySelectorAll('[data-edge-from]')){if([p.dataset.edgeFrom,p.dataset.edgeTo].includes(m.selected)){related.add(p.dataset.edgeFrom);related.add(p.dataset.edgeTo)}let id=p.dataset.edgeId;let changed=(m.relations||[]).some(r=>{let key=typeof r==='string'?r:r.id;return key&&id==='r-'+Array.from(new TextEncoder().encode(key),b=>b.toString(16).padStart(2,'0')).join('')});p.classList.toggle('lac-relation',changed)}
for(let n of nodes){let id=n.dataset.nodeId;n.classList.toggle('lac-dirty',dirty.has(id));n.classList.toggle('lac-pending',pending.has(id));n.classList.toggle('lac-indirect',indirect.has(id)&&!dirty.has(id));n.classList.toggle('lac-selected',id===m.selected);n.classList.toggle('lac-dim',!!m.focused&&!!m.selected&&!related.has(id))}
let view=Archify.view;if(m.action==='in')view?.zoomIn();if(m.action==='out')view?.zoomOut();if(m.action==='fit')view?.reset();
if(m.restore&&view){let scale=Number(m.restore.scale),v=m.restore.viewport;if(Number.isFinite(scale)&&scale>=1&&scale<=3&&v&&[v.x,v.y,v.width,v.height].every(Number.isFinite)){applying=true;view.centerAt(v.x+v.width/2,v.y+v.height/2,{scale,instant:true});applying=false;}}
});
new MutationObserver(navigation).observe(svg,{attributes:true,attributeFilter:['style']});window.addEventListener('pointerup',navigation);window.addEventListener('keyup',navigation);
emit('lac-ready');
})();
</script>"""


def handler(config_path: Path, explicit: str | None, observer: DevelopmentObserver | None = None):
    directory = archctx.state(config_path, explicit).resolve()
    try:
        config = archctx.load(config_path)
        repo = archctx.repo_for(config_path, config)
    except (OSError, ValueError):
        # An invalid edit must not prevent opening the retained accepted view.
        repo = Path(archctx.load(archctx.last_path(directory))["repo"]).resolve()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def reply(self, code: int, value: str | bytes, kind: str = "text/html; charset=utf-8", etag: str | None = None):
            body = value.encode("utf-8") if isinstance(value, str) else value
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if etag:
                self.send_header("ETag", etag)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            # Loopback binding plus Host check blocks DNS rebinding; there is no write API.
            if self.headers.get("Host") not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
                self.reply(403, "Local blueprint host required", "text/plain")
                return
            url = urlsplit(self.path)
            try:
                if url.path == "/":
                    self.reply(200, PAGE)
                    return
                development = None
                if observer and url.path == "/api/current":
                    development, record = observer.bundle()
                    record = record or {}
                    notice = development.get("updates", {})
                    value = {"context": record.get("context", {}), "last_good_context_hash": record.get("context_hash"),
                             "archify": record.get("archify", {}), "revision": record.get("revision"),
                             "graph": record.get("graph", {}), "status": development.get("status", notice.get("status", "UNAVAILABLE")),
                             "reason": [development["error"]] if development.get("error") else notice.get("reason", notice.get("failures", []))}
                else:
                    value = archctx.snapshot(config_path, explicit)
                receipt = value.get("archify", {})
                if url.path == "/api/current":
                    result = {key: value.get(key) for key in ("status", "revision", "reason", "graph")}
                    result.update(context_hash=value.get("last_good_context_hash"), generation=Path(receipt.get("generation", "")).name,
                                  artifacts=list(receipt.get("artifacts", {})), delta_kind=receipt.get("delta_kind"),
                                  before_reason=receipt.get("before_reason"), before_available=receipt.get("before_available", False),
                                  coverage=value.get("context", {}).get("coverage"), components=value.get("context", {}).get("components", []),
                                  relations=value.get("context", {}).get("relations", []), development=development)
                    etag = '"' + archctx.semantic({key: result.get(key) for key in ("context_hash", "generation", "status", "reason")} |
                        {"observation": (development or {}).get("observation_id"), "refresh": (development or {}).get("refresh"),
                         "observer_running": (development or {}).get("observer_running")}) + '"'
                    if self.headers.get("If-None-Match") == etag:
                        self.reply(304, b"", etag=etag)
                    else:
                        before = archctx.snapshot_record(directory, receipt.get("previous_context_hash") or "")
                        result["recent_delta"] = archctx.record_diff(before, {"context": value["context"]}) if before else {}
                        self.reply(200, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8", etag=etag)
                    return
                if url.path.startswith(("/artifact/", "/map/")):
                    if receipt.get("render") == "PASS" and (receipt.get("context_hash") != value.get("last_good_context_hash")
                            or receipt.get("revision") != value.get("revision")):
                        raise ValueError("artifact receipt does not belong to this accepted context")
                    if failure := archctx.archify_artifact_reason(directory, receipt):
                        raise ValueError(failure)
                    parts = url.path.split("/")
                    if len(parts) != 4 or parts[2] != Path(receipt.get("generation", "")).name:
                        self.reply(409, "Blueprint version changed; reload the current view", "text/plain")
                        return
                    path = artifact_path(repo, directory, receipt, parts[3])
                    artifact = path.read_bytes()
                    if archctx.sha(artifact) != receipt["artifacts"][parts[3]]:
                        raise ValueError("blueprint artifact changed while reading")
                    if url.path.startswith("/map/"):
                        if parts[3] not in ("current.html", "comparison.html"):
                            raise ValueError("only accepted HTML maps support the local viewer")
                        bridge = MAP_BRIDGE if parts[3] == "current.html" else "<script>if(document.querySelector('[data-view=delta] svg'))parent.postMessage({kind:'lac-ready'},'*')</script>"
                        self.reply(200, artifact.decode("utf-8") + bridge)
                    else:
                        self.reply(200, artifact, mimetypes.guess_type(path.name)[0] or "application/octet-stream")
                    return
                if url.path == "/source":
                    if archctx.repo_for(config_path, archctx.load(config_path)) != repo:
                        raise ValueError("repository changed; restart the viewer for source evidence")
                    args = parse_qs(url.query)
                    if args.get("context", [""])[0] != value.get("last_good_context_hash"):
                        self.reply(409, "Accepted context changed; reopen this evidence from the blueprint", "text/plain")
                        return
                    component = next(c for c in value["context"]["components"] if c["id"] == args.get("component", [""])[0])
                    index = int(args.get("item", ["0"])[0])
                    if index < 0:
                        raise ValueError("invalid evidence index")
                    evidence = component["evidence"][index]
                    path, relative = archctx.repo_file(repo, evidence["path"], "evidence.path")
                    source = path.read_text(encoding="utf-8", errors="replace")
                    if archctx.sha(source.encode()) != evidence["sha256"]:
                        self.reply(409, "Source has changed since this accepted evidence; verify the worktree or refresh", "text/plain")
                        return
                    first = max(0, evidence["line"] - 6)
                    lines = source.splitlines()[first:first + 25]
                    snippet = "\n".join(f"{first+i+1}: {line}" for i, line in enumerate(lines))
                    self.reply(200, '<meta charset="utf-8"><title>Source evidence</title><h2>'+html.escape(relative)+
                               '</h2><p>Accepted evidence · '+html.escape(evidence["sha256"])+
                               '</p><pre>'+html.escape(snippet)+'</pre>')
                    return
                if url.path == "/working-source" and observer:
                    if archctx.repo_for(config_path, archctx.load(config_path)) != repo:
                        raise ValueError("repository changed; restart the viewer")
                    development, _ = observer.bundle()
                    args = parse_qs(url.query)
                    if args.get("observation", [""])[0] != development.get("observation_id") or development.get("error"):
                        raise ValueError("worktree observation changed; reopen the source from the map")
                    change = next(x for x in development.get("changes", []) if x["path"] == args.get("path", [""])[0])
                    if not change.get("sha256"):
                        raise ValueError("source is outside the content-verified observation scope")
                    path, relative = archctx.repo_file(repo, change["path"], "observed.path")
                    if path.stat().st_size > archctx.WATCH_SOURCE_BYTES:
                        raise ValueError("source exceeds the bounded observation scope")
                    raw = path.read_bytes()
                    if archctx.sha(raw) != change["sha256"]:
                        raise ValueError("source changed after observation; wait for the next map update")
                    snippet = "\n".join(f"{i+1}: {line}" for i, line in enumerate(raw.decode("utf-8", errors="replace").splitlines()[:80]))
                    self.reply(200, '<meta charset="utf-8"><title>Working source · not accepted</title><h2>'+html.escape(relative)+
                               '</h2><p>Observed working source, not accepted evidence. First 80 lines. SHA-256 '+html.escape(change["sha256"])+
                               '</p><pre>'+html.escape(snippet)+'</pre>')
                    return
                self.reply(404, "Not found", "text/plain")
            except (KeyError, IndexError, StopIteration, ValueError, OSError) as error:
                self.reply(409, "Blueprint unavailable: " + html.escape(str(error)))
    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="architecture/architecture.json")
    parser.add_argument("--state-dir")
    parser.add_argument("--port", type=int, default=0, help="0 selects an unused local port")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--live", action="store_true", help="observe saved changes; validate/promote settled shared config/view edits using existing refresh")
    args = parser.parse_args()
    path = Path(args.config).resolve()
    observer = DevelopmentObserver(path, args.state_dir, live=args.live).start()
    server = None
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(path, args.state_dir, observer))
        url = f"http://127.0.0.1:{server.server_port}/"
        print(json.dumps({"url": url, "mode": "live" if args.live else "read_only", "stop": "Ctrl+C stops this viewer and its own observer",
                          "next_action": "keep developing; Codex maintains shared semantics, the page follows accepted updates"}), flush=True)
        if not args.no_open:
            webbrowser.open(url)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        if server:
            server.server_close()
        observer.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
