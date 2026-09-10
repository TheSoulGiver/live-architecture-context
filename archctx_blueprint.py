#!/usr/bin/env python3
"""Render accepted architecture with Archify and serve its local, live view."""
from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import re
import shlex
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import archctx
import archctx_understand as understand
from archctx_development import DevelopmentObserver

MAX_ARTIFACT_BYTES = 8 * 1024 * 1024


def analysis_source(repo: Path, directory: Path, args: dict[str, list[str]]) -> str:
    """Read only a current receipt's captured source, never arbitrary worktree files."""
    receipt_path = understand.current_path(directory)
    receipt = understand.local_json(receipt_path, understand.SUMMARY_BYTES)
    if not isinstance(receipt.get("source_hashes"), dict):
        raise ValueError("invalid analysis source manifest")
    ident, relative, digest = (args.get(key, [""])[0] for key in ("analysis", "path", "sha"))
    if not re.fullmatch(r"[0-9a-f]{64}", ident) or ident != receipt["analysis_id"] or Path(receipt["worktree"]).resolve() != repo:
        raise ValueError("analysis version/worktree changed; reopen the source analysis")
    if not re.fullmatch(r"[0-9a-f]{64}", digest) or receipt["source_hashes"].get(relative) != digest:
        raise ValueError("source is outside this content-bound analysis")
    runs = (directory / "understand/runs").resolve()
    runs.relative_to(directory.resolve())
    source = (runs / ident / "source").resolve()
    source.relative_to(runs)
    if Path(receipt["source_root"]).resolve() != source:
        raise ValueError("analysis snapshot location changed")
    path, normalized = archctx.repo_file(source, relative, "analysis source")
    if normalized != relative or {p.casefold() for p in Path(relative).parts + path.relative_to(source).parts}.intersection({".git", ".archctx", ".ua"}):
        raise ValueError("invalid captured source path")
    raw = understand.bounded_raw(path, understand.SOURCE_BYTES)
    if archctx.sha(raw) != digest or understand.local_json(receipt_path, understand.SUMMARY_BYTES) != receipt:
        raise ValueError("captured source or analysis receipt changed while reading")
    lines = raw.decode("utf-8", errors="replace").splitlines()
    line = int(args.get("line", ["1"])[0])
    if not 1 <= line <= max(1, len(lines)):
        raise ValueError("invalid captured source line")
    first = max(0, line - 6)
    snippet = "\n".join(f"{first+i+1}: {text}" for i, text in enumerate(lines[first:first + 25]))
    return ('<meta charset="utf-8"><title>Historical analysis source · not accepted</title><h2>' + html.escape(relative)
            + '</h2><p>Historical captured analysis source · not accepted architecture evidence · not current worktree</p><p>Analysis '
            + ident + ' · SHA-256 ' + digest + '</p><pre>' + html.escape(snippet) + '</pre>')


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
<title>系统地图 · LAC</title><style>
:root{--ink:#203a37;--muted:#637773;--line:#d6dfd6;--paper:#f5f5ed;--green:#176958;--amber:#a95412;--red:#ae3e38}
*{box-sizing:border-box}body{margin:0;background:var(--paper);color:var(--ink);font:14px "Segoe UI","Microsoft YaHei",sans-serif}
header{height:102px;display:flex;align-items:center;justify-content:space-between;padding:18px 28px;border-bottom:1px solid var(--line);gap:20px}
.eyebrow{font:11px Consolas,monospace;letter-spacing:2px;color:var(--green)}h1{font:29px Georgia,"Microsoft YaHei",serif;letter-spacing:-.5px;margin:5px 0}h2{font-size:17px;margin:0 0 12px}h3{font-size:14px;margin:19px 0 8px}
small,.muted{color:var(--muted)}p{line-height:1.65;margin:8px 0}code{font:12px Consolas,monospace;overflow-wrap:anywhere}a{color:var(--green);text-decoration-thickness:1px;text-underline-offset:3px}
button,select{font:inherit;color:inherit;background:transparent;border:1px solid var(--line);border-radius:5px;padding:8px 12px;cursor:pointer}button:hover,button[aria-pressed=true]{background:#e2eadd;border-color:#648976}button:focus-visible,a:focus-visible,summary:focus-visible{outline:3px solid #d8942b;outline-offset:3px}button:disabled{cursor:default;opacity:.45}[hidden]{display:none!important}
.status{font-size:12px;display:flex;align-items:center;gap:8px;justify-content:flex-end}.dot{width:8px;height:8px;border-radius:50%;background:var(--green)}.stale .dot{background:var(--amber)}#identity{display:block;font:11px Consolas,monospace;max-width:360px;margin-top:8px;text-align:right}
nav{height:54px;display:flex;align-items:center;gap:7px;padding:8px 24px;background:#fffdf7;border-bottom:1px solid var(--line)}nav .spacer{flex:1}nav a{font-size:12px}
main{display:grid;grid-template-columns:minmax(0,1fr) 370px;height:calc(100vh - 190px);min-height:430px}.canvas{min-width:0;position:relative;display:flex;flex-direction:column;background:#fff;border-right:1px solid var(--line)}
.canvas-head{padding:16px 24px 10px;display:flex;justify-content:space-between;gap:12px}.canvas-head h2{margin:0 0 5px}.legend{font-size:11px;display:flex;gap:12px;flex-wrap:wrap;margin-top:8px}.legend span:before{content:'';display:inline-block;border:2px solid #6b8f80;width:10px;height:10px;margin-right:5px}.legend .direct:before{border-color:#b66a13}.legend .dependency:before{border-style:dashed;border-color:#526fac}.legend .dependent:before{border-style:dotted;border-color:#267b79}.legend .both:before{border-style:dashed;border-color:#75578e}
#stage{position:relative;flex:1;min-height:0}iframe{width:100%;height:100%;border:0;background:#fff}.map-controls{display:flex;gap:5px;align-items:center;align-self:flex-start}.map-controls button{padding:5px 10px;font-size:12px}
#empty{position:absolute;inset:25% 15%;text-align:center;color:var(--muted);pointer-events:none}#empty[hidden]{display:none}#reason{padding:9px 24px;background:#fff3dd;color:#794917;font-size:12px;line-height:1.5;border-top:1px solid #ecd4aa;max-height:86px;overflow:auto}#reason:empty{display:none}
aside{padding:20px;overflow:auto;min-width:0;overflow-wrap:anywhere;background:#fafbf6}#summary{font-size:12px;line-height:1.65;color:var(--muted);border-bottom:1px solid var(--line);padding-bottom:15px;margin-bottom:18px}.section-label{font-size:11px;letter-spacing:1px;color:var(--muted);margin-bottom:8px}
.change{border-left:3px solid #c27c27;padding:9px 10px;background:#fff8e9;margin:8px 0;font-size:12px}.change.unmapped{border-left-style:dashed;border-color:#99a4a1;background:#eff1ec}.change.pending{border-color:#ad5575;background:#f8edf2}.change.accepted{border-color:var(--green);background:#eaf3ea}.change button{padding:3px 6px;font-size:11px;margin:6px 4px 0 0}.change code{display:block}
.scope-group{border-top:1px solid var(--line);padding-top:8px;margin-top:9px}.scope-group.pending{border-left:2px dashed #ad5575;padding-left:8px}.scope-row{margin:6px 0;line-height:1.6}.scope-group details{margin:8px 0}.witness{padding:7px 0;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.scope-note{font-size:11px;color:var(--muted);overflow-wrap:anywhere}
.tag{display:inline-block;font-size:10px;color:var(--muted);border:1px solid var(--line);padding:2px 5px;border-radius:3px;margin:3px 4px 3px 0}.node-button{display:block;width:100%;text-align:left;padding:10px 8px;border-width:0 0 1px;border-radius:0}.node-button small{display:block;margin-top:4px;font-size:11px}
.state-tag{display:inline-block;padding:3px 6px;margin:4px 5px 3px 0;font-size:11px;background:#e5eee3;color:var(--green);border-radius:3px}.state-tag.discovered{background:#f4e8ed;color:#914562}.state-tag.changed{background:#fff0d6;color:#87520e}.project-stats{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:12px 0 18px}.project-stats button{padding:10px 5px;text-align:left;font-size:11px}.project-stats strong{display:block;font:27px Georgia,serif;margin-bottom:5px}.query{display:block;white-space:pre-wrap;background:#edf1e8;padding:9px;margin:7px 0;border-left:2px solid #8ca58f;user-select:all}.finding{border-top:1px solid var(--line);padding:8px 0}.finding summary{line-height:1.6}.flow-row{border-left:2px solid #a3b8a9;padding:0 0 10px 12px;margin:12px 0}.flow-row button{padding:4px 6px;font-size:12px}.flow-row code{display:block;margin-top:7px}
#detail{margin-top:12px;padding-top:16px;border-top:1px solid var(--line)}#detail:empty{display:none}#detail .name{font-size:16px;font-weight:600}details{margin:12px 0}summary{cursor:pointer;font-size:12px;font-weight:600}details li{margin:7px 0;font-size:12px;line-height:1.55}ul{padding-left:18px}.source{display:block;margin:8px 0;font:11px Consolas,monospace;overflow-wrap:anywhere}
footer{height:34px;border-top:1px solid var(--line);display:flex;justify-content:space-between;gap:12px;padding:9px 24px;font-size:10px;color:var(--muted)}#observation{font-family:Consolas,monospace}
@media(max-width:950px){header{padding:14px 16px}h1{font-size:24px}main{grid-template-columns:minmax(0,1fr) 280px}.canvas-head{padding:12px}aside{padding:14px}.canvas-head small{display:none}}
@media(max-width:680px){header{height:auto;min-height:110px;align-items:flex-start;flex-wrap:wrap;gap:9px}header small{display:none}#identity{max-width:none;text-align:left}.status{justify-content:flex-start}h1{font-size:21px}nav{height:auto;min-height:54px;padding:7px;gap:3px;flex-wrap:wrap}nav button{padding:7px;font-size:12px}nav a,nav .spacer{display:none}main{height:auto;display:flex;flex-direction:column}.canvas{height:62vh;min-height:360px}aside{max-height:none}footer{height:auto;padding:10px;flex-wrap:wrap}.canvas-head{gap:5px;flex-wrap:wrap}.map-controls{flex-wrap:wrap}}
</style>
<header><div><div class="eyebrow">LAC / SYSTEM MAP</div><h1 id="project-name">系统地图</h1><small>人与 Agent 共用组件身份、源码证据和变化范围</small></div><div><div class="status" id="status"><i class="dot"></i><span id="state">正在读取已接受蓝图</span></div><code id="identity"></code></div></header>
<nav aria-label="地图视图"><button id="now" aria-pressed="true">项目</button><button id="component-view" aria-pressed="false">组件</button><button id="flow-view" aria-pressed="false">流程</button><button id="recent" aria-pressed="false">变化</button><button id="delta" aria-pressed="false">Before / After</button><button id="all-changes" aria-pressed="true">全部修改</button><span class="spacer"></span><a id="raw" target="_blank" rel="noopener">打开原始图 ↗</a></nav>
<main><section class="canvas"><div class="canvas-head"><div><h2 id="map-title">已接受的系统蓝图</h2><small id="map-caption">点击组件，查看职责、原始关系和源码。</small><div class="legend"><span>已接受</span><span class="direct">直接涉及</span><span class="dependency">依赖对象</span><span class="dependent">依赖者</span><span class="both">两者重合</span></div></div><div class="map-controls"><button id="zoom-out" aria-label="缩小">−</button><button id="zoom-in" aria-label="放大">＋</button><button id="fit">全景</button><button id="focus" aria-pressed="false">聚焦邻居</button></div></div>
<div id="stage"><iframe id="picture" title="Archify 系统图与开发变化" sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"></iframe><div id="empty">等待可用的已接受 Archify 图。<p>局部变化不会被猜成正式架构。</p></div></div><div id="reason" role="status"></div></section>
<aside><div class="section-label" id="panel-title">项目概览</div><div id="summary"></div><section id="project" aria-label="项目概览"></section><div id="detail"></div><section id="analysis" aria-label="相关发现"></section><section id="flows" aria-label="声明流程" hidden></section><div id="changes" hidden></div><details id="components-list"><summary>全部组件</summary><div id="sources"></div></details><details id="coverage"><summary>覆盖范围与可信边界</summary><p id="scope"></p><ul id="limits"></ul><p id="graph" class="muted"></p></details></aside></main>
<footer><span id="observation">OBSERVATION —</span><span>文件活动 ≠ 任务进度 ≠ 线上运行 · 本地只读页面</span></footer>
<script>
let data=null,latest=null,mode='now',panel='now',selected=null,selectedChange=null,focused=false,version='',navigation=null,lastObservation='',detailVersion='',etag='',loading=null,loadingKey='',loadTimer;
const $=id=>document.getElementById(id),enc=encodeURIComponent;
let picture=$('picture');
const nodeId=id=>'c-'+Array.from(new TextEncoder().encode(id),b=>b.toString(16).padStart(2,'0')).join('');
const el=(tag,text,cls)=>{let e=document.createElement(tag);if(text!==undefined)e.textContent=text;if(cls)e.className=cls;return e};
const unique=items=>[...new Set(items)];
const changeScope=change=>{let s=change.change_scope;return s?.contract==='declared_change_scope/v1'&&s.accepted_context_hash===data?.context_hash?s:null};
const analysis=()=>data?.development?.updates?.source_analysis;
const relatedFindings=id=>(analysis()?.candidates||[]).filter(c=>(c.related_components||[]).includes(id));
const sourceChanged=c=>(data?.development?.changes||[]).some(x=>{let s=changeScope(x);return s&&s.observation_state!=='retained_previous_observation'&&(s.accepted?.direct_components||[]).includes(c.id)});
function findingState(c,a){if(a.retained_previous_observation||!['FRESH','STALE'].includes(a.status))return ['分析状态未知','changed'];if((a.changed_files||[]).some(path=>(c.related_source_files||c.files||[]).includes(path)||(c.evidence||[]).some(e=>e.path===path)))return ['源码已变化 · 待重新理解','changed'];return c.review_state==='accepted'?['已复审发现','']:c.review_state==='rejected'?['已排除发现','']:['已发现 · 待复审','discovered'];}
function componentButton(c){let b=el('button',c.name||c.id,'node-button');b.setAttribute('aria-pressed',c.id===selected);b.append(el('small',c.id),el('span','已确认','state-tag'));if(sourceChanged(c))b.append(el('span','源码已变化','state-tag changed'));let count=relatedFindings(c.id).length;if(count)b.append(el('span',count+' 条相关发现','state-tag discovered'));b.onclick=()=>select(c.id);return b;}
function panels(){for(let id of ['now','component-view','flow-view','recent','delta'])$(id).setAttribute('aria-pressed',id===panel);$('panel-title').textContent=({now:'项目概览','component-view':'组件 / 同一身份','flow-view':'流程 / 声明关系',recent:'变化 / 工作区与已接受版本',delta:'变化 / 版本比较'})[panel];$('project').hidden=panel!=='now';$('detail').hidden=panel!=='component-view';$('flows').hidden=panel!=='flow-view';$('changes').hidden=!['recent','delta'].includes(panel);$('components-list').hidden=!['now','component-view'].includes(panel);if(panel==='component-view')$('components-list').open=true;}
function drawProject(){let box=$('project'),a=analysis(),components=data.components||[],d=data.development||{};box.replaceChildren();let stats=el('div',undefined,'project-stats');for(let [count,label,target] of [[components.length,'已确认组件','component-view'],[a?.candidate_count||0,'源码发现','component-view'],[d.counts?.changed??(d.changes||[]).length,'已保存变化','recent']]){let b=el('button');b.append(el('strong',String(count)),el('span',label));b.onclick=()=>{if(target==='component-view'){selected=null;detailVersion=''}showPanel(target)};stats.append(b)}box.append(stats,el('p',data.coverage?.scope||'从组件进入系统，沿声明关系找到源码依据。'));let q=data.project?.queries;if(q){let details=el('details');details.append(el('summary','Agent 从同一项目继续'),el('p','在以下项目目录运行：','scope-note'),el('code',data.project.root,'query'),el('code',q.updates,'query'));box.append(details)}}
function drawFlows(){let box=$('flows');box.replaceChildren(el('h2','组件之间如何连接'),el('p','箭头保留声明方向；依赖语义单独标明。这里不是完整运行调用链。','scope-note'));for(let r of data.relations||[]){let row=el('div',undefined,'flow-row');for(let [i,id] of [r.from,r.to].entries()){if(i)row.append(el('span',' → '));let c=data.components?.find(c=>c.id===id),b=el('button',c?.name||id);b.onclick=()=>select(id);row.append(b)}row.append(el('code',(r.id||r.from+' → '+r.to)+' · '+r.kind),el('p',r.dependency==='from_to'?r.from+' 依赖 '+r.to:r.dependency==='to_from'?r.to+' 依赖 '+r.from:r.dependency==='none'?'已声明为非依赖关系':'依赖语义尚未分类','scope-note'));for(let e of r.evidence||[])row.append(el('code',e.path+(e.line?':'+e.line:''),'source'));box.append(row)}if(!data.relations?.length)box.append(el('p','尚无已确认的关系声明。'));let tour=analysis()?.tour;if(tour?.length){let d=el('details');d.append(el('summary','已发现的源码导览 · 待核对流程'));for(let step of tour)d.append(el('h3',step.order+'. '+step.title),el('p',step.description));box.append(d)}}
function send(extra={}){if(!data)return;let scopes=(data.development?.changes||[]).filter(x=>selectedChange===null||x.path===selectedChange).map(changeScope).filter(s=>s&&s.observation_state!=='retained_previous_observation').map(s=>s.accepted).filter(s=>s&&!s.error);
 const ids=key=>unique(scopes.flatMap(s=>s[key]||[])).filter(id=>data.components?.some(c=>c.id===id)).map(nodeId);
 picture.contentWindow.postMessage({kind:'lac-map',context:data.context_hash,selected:selected?nodeId(selected):null,focused,
 direct:ids('direct_components'),dependencies:ids('dependencies'),dependents:ids('dependents'),...extra},'*');}
function select(id){if(!(data?.components||[]).some(c=>c.id===id))return;selected=id;panel='component-view';detailVersion='';detail();drawAnalysis();panels();$('sources').replaceChildren(...(data.components||[]).map(componentButton));send();}
function focusChange(path){selectedChange=path;paint(data);send();}
function detail(){let key=data?.context_hash+':'+data?.development?.observation_id+':'+selected;if(key===detailVersion)return;detailVersion=key;let expanded=$('detail').dataset.component===selected?($('detail').querySelector('details')?.open??true):true;$('detail').replaceChildren();$('detail').dataset.component=selected||'';let c=data?.components?.find(c=>c.id===selected);if(!c){$('detail').append(el('p','选择图中组件，或从下方列表进入。','muted'));return;}
 let out=$('detail');out.append(el('div',c.name||c.id,'name'),el('code',c.id),el('span','已确认组件','state-tag'));if(sourceChanged(c))out.append(el('span','源码已变化','state-tag changed'));out.append(el('p',c.purpose||''));
 for(let t of c.tags||[])out.append(el('span',t,'tag'));
 for(let [title,key,other] of [['原始入向关系 · 不等于依赖者','to','from'],['原始出向关系 · 不等于依赖对象','from','to']]){let relations=(data.relations||[]).filter(r=>r[key]===c.id);out.append(el('h3',title));if(!relations.length)out.append(el('small','当前声明未覆盖此关系'));for(let r of relations){let b=el('button',r[other]+' · '+r.kind,'node-button');b.onclick=()=>select(r[other]);out.append(b);}}
 let d=el('details');d.open=expanded;d.append(el('summary','源码证据 · '+(c.evidence||[]).length+' 个锚点'));
 for(let [i,e] of (c.evidence||[]).entries()){let a=el('a',e.path+':'+e.line,'source');a.target='_blank';a.rel='noopener';a.href='/source?component='+enc(c.id)+'&item='+i+'&context='+enc(data.context_hash);d.append(a)}out.append(d);
 let q=data.project?.component_queries?.[c.id];if(q){let queries=el('details');queries.append(el('summary','Agent 查询 · 同一个组件 ID'),el('p','在 '+data.project.root+' 运行；查询不触发模型。','scope-note'));for(let command of [data.project.queries?.updates,q.canonical,q.evidence,q.trace])if(command)queries.append(el('code',command,'query'));queries.append(el('p','接受版本 '+data.context_hash+'；先用 updates 核对接受版本，再读取源码。','scope-note'));out.append(queries)}
}
function card(title,path,owners,cls=''){let c=el('div',undefined,'change '+cls);c.append(el('div',title));if(path)c.append(el('code',path));for(let id of owners||[]){let owner=data.components?.find(x=>x.id===id);if(!owner){c.append(el('span','待确认 · '+id,'tag'));continue}let b=el('button',owner.name||id);b.onclick=()=>select(id);c.append(b)}return c;}
function scopeGroup(parent,label,scope,working=false){let box=el('div',undefined,'scope-group'+(working?' pending':''));box.append(el('div',label));parent.append(box);
 if(!scope||scope.error){box.append(el('p',scope?.error||'没有可用的已接受声明范围。','scope-note'));return;}
 for(let [key,title] of [['direct_components','直接涉及'],['dependencies','依赖对象'],['dependents','依赖者']]){let row=el('div',undefined,'scope-row');row.append(el('span',title+'：'));let ids=scope[key]||[];
 if(!ids.length)row.append(el('small',key==='direct_components'?'未匹配直接归属':'当前已分类关系中未列出'));for(let id of ids){let b=el(working?'span':'button',id,working?'tag':'');if(!working)b.onclick=()=>select(id);row.append(b)}box.append(row);}
 if(scope.uncovered_files?.length)box.append(el('p','未覆盖路径：'+scope.uncovered_files.join('、'),'scope-note'));
 let omitted=Object.entries(scope.omitted||{}).filter(([,count])=>count>0);if(omitted.length)box.append(el('p','紧凑范围有省略：'+omitted.map(([key,count])=>key+' '+count).join('；')+'。未列出不代表不存在。','scope-note'));
 let details=el('details');details.append(el('summary','关系与路径依据 · '+(scope.relations||[]).length+' 条关系'));
 for(let r of scope.relations||[]){let witness=el('div',undefined,'witness'),direction=r.dependency==='from_to'?r.from+' 依赖 '+r.to:r.dependency==='to_from'?r.to+' 依赖 '+r.from:r.dependency==='none'?'显式非依赖关系':'未分类；不据此推断依赖';
 witness.append(el('code',r.from+' → '+r.to+' · '+r.kind),el('div',direction),el('small',(r.id||'')+' · '+({explicit:'显式依赖语义',kind_default:'kind 默认语义',unclassified:'语义未分类'}[r.semantics]||r.semantics)+' · '+({accepted_source_evidence:'已接受源码锚点',working_anchor_unvalidated:'工作区锚点未验证',authored_only:'仅架构声明'}[r.evidence_state]||r.evidence_state)));
 for(let e of r.evidence||[])witness.append(el('code',e.path+(e.line?':'+e.line:'')));if(r.omitted_evidence>0)witness.append(el('p','另有 '+r.omitted_evidence+' 个锚点；使用 --details 查看。','scope-note'));details.append(witness);}
 for(let a of scope.associations||[])details.append(el('p',a.component+' ← '+(a.paths||[]).join('、'),'scope-note'));
 if(scope.config_seeds?.length)details.append(el('p','配置变更涉及：'+scope.config_seeds.join('、'),'scope-note'));box.append(details);
}
function drawAnalysis(){let a=analysis(),box=$('analysis');box.replaceChildren();box.hidden=!['now','component-view'].includes(panel);if(box.hidden)return;
 box.append(el('h2',selected&&panel==='component-view'?'这个组件的相关发现':'源码理解'));
 if(!a?.configured){box.append(el('p','尚未进行源码理解。让 Agent 选择文件与问题后运行 understand；页面读取不会启动分析。','scope-note'));return;}
 box.append(el('p',a.status==='FRESH'?'分析 FRESH · 捕获源码仍一致':(a.status||'UNKNOWN')+' · 分析源码需要复查','scope-note'));
 if(a.reason)box.append(el('p',a.reason,'scope-note'));if(a.changed_files?.length)box.append(el('p','分析后已变化：'+a.changed_files.join('、'),'scope-note'));if(a.retained_previous_observation)box.append(el('p','保留上次观察；当前分析状态未知。','scope-note'));
 let findings=selected&&panel==='component-view'?relatedFindings(selected):(a.candidates||[]);
 if(!findings.length)box.append(el('p',selected?'当前有界结果没有关联到此组件的发现。':'当前没有待展示的源码发现。','scope-note'));
 for(let c of findings){let d=el('details',undefined,'finding'),state=findingState(c,a),summary=el('summary',c.title||c.id);summary.append(el('span',state[0],'state-tag '+state[1]));d.dataset.finding=c.id;d.append(summary,el('p',c.summary||''),el('code',c.id,'source'));
 let bindings=c.review_state==='accepted'?(c.bindings||[]):[];d.append(el('p',bindings.length?'已复审绑定：'+bindings.join('、'):'源码交集只定位调查范围；尚未确认归属。','scope-note'));
 for(let id of c.related_components||[]){let component=data.components?.find(x=>x.id===id);if(component){let b=el('button',component.name||id);b.onclick=()=>select(id);d.append(b)}}
 for(let e of c.evidence||[]){let link=el('a',e.path+':'+e.line+' · 捕获时的源码','source');link.target='_blank';link.rel='noopener';link.href='/analysis-source?analysis='+enc(a.analysis_id)+'&path='+enc(e.path)+'&sha='+enc(e.sha256)+'&line='+enc(e.line);d.append(link)}
 for(let r of c.raw_relations||[])d.append(el('p',r.source+' → '+r.target+' · '+r.type+' · direction='+r.direction,'scope-note'));
 if(c.omitted_raw_relations||c.omitted_evidence)d.append(el('p','省略关系 '+(c.omitted_raw_relations||0)+' / 锚点 '+(c.omitted_evidence||0)+'；使用分析查询查看范围。','scope-note'));
 d.append(el('p','内容 '+(c.content_revision||'unknown')+' · 证据 '+(c.evidence_revision||'unknown'),'scope-note'));box.append(d);}
 let diagnostics=el('details');diagnostics.append(el('summary','分析来源与覆盖边界'),el('code',(a.provider?.name||'unknown')+' @ '+(a.provider?.revision||'unknown')),el('p','分析 '+(a.analysis_id||'unknown')+' · '+(a.source_files||[]).length+' 个选定文件','scope-note'),el('p','源码分组与导览是调查材料，不是 canonical 组件；复审状态不替代已接受架构的源码新鲜度。','scope-note'));
 for(let limitation of a.limitations||[])diagnostics.append(el('p',limitation,'scope-note'));if(data.project?.queries?.analysis)diagnostics.append(el('code',data.project.queries.analysis,'query'));box.append(diagnostics);
 if(a.details_omitted||a.omitted_candidate_count||a.omitted_tour_steps)box.append(el('p','有界页面已省略部分分析；完整查询见来源与覆盖边界。','scope-note'));
}
function drawChanges(){let d=data.development||{},p=d.pending||{},box=$('changes');box.replaceChildren();
 if(mode==='recent'){let delta=data.recent_delta||{},changed=delta.changed_components||[];box.append(el('h2','最近接受的变化'));if(data.before_available){box.append(card('已接受 · '+(data.delta_kind||'unknown'),'',changed,'accepted'));for(let [key,label] of [['added_relations','新增关系'],['removed_relations','移除关系'],['changed_relations','关系语义变化']])for(let r of delta[key]||[])box.append(card(label,typeof r==='string'?r:(r.id||r.from+' → '+r.to),[],'accepted'));if(delta.evidence_changed_components?.length)box.append(card('源码证据更新（不等于新增架构）','',delta.evidence_changed_components,'accepted'));}else box.append(el('p',data.before_reason||'没有可用的上一接受版本。'));}
 {box.append(el('h2','工作区的变化'));let labels={add:'新增文件',modify:'已保存修改',delete:'已删除',rename:'已重命名',untracked:'未跟踪文件'};
 for(let x of d.changes||[]){let s=changeScope(x),c=card(labels[x.kind]||x.kind,x.old_path?x.old_path+' → '+x.path:x.path,[]),b=el('button','图中查看此修改');b.setAttribute('aria-pressed',selectedChange===x.path);b.disabled=version.endsWith(':comparison.html');b.onclick=()=>focusChange(x.path);c.append(b);
 if(s){c.append(el('small','声明复审范围 · 非运行影响 · '+s.freshness));if(s.observation_state==='retained_previous_observation')c.append(el('p','保留上次观察，当前修改范围未知；不高亮历史范围。','scope-note'));scopeGroup(c,'已接受图 · '+(s.accepted_context_hash||'none').slice(0,12),s.accepted);
 if(s.working!==null&&s.working!==undefined)scopeGroup(c,'工作区声明 · 尚未接受 · '+(s.working_config_hash||'unknown').slice(0,12),s.working,true);
 for(let limitation of s.limitations||[])c.append(el('p',limitation,'scope-note'));c.append(el('p','完整范围：'+(s.detail_query||'impact 使用相同文件加 --details；MCP details:true'),'scope-note'));}
 else c.append(el('p','共享范围暂不可用或版本已变；不从旧关联字段推断依赖。','scope-note'));
 if(x.sha256){let a=el('a','查看当前源码 ↗','source');a.href='/working-source?path='+enc(x.path)+'&observation='+enc(d.observation_id);a.target='_blank';a.rel='noopener';c.append(a)}box.append(c)};
 if(!(d.changes||[]).length)box.append(el('p','当前没有观察到文件变化。','muted'));
 let pendingBox=el('div');
 for(let [key,label] of [['added_components','新增组件待确认'],['removed_components','移除组件待确认'],['changed_components','职责 / 入口待确认']])if(p[key]?.length)pendingBox.append(card(label,'',p[key],'pending'));
 for(let [key,label] of [['added_relations','新增关系待确认'],['removed_relations','移除关系待确认'],['changed_relations','关系变化待确认']])for(let r of p[key]||[])pendingBox.append(card(label,typeof r==='string'?r:(r.id||r.from+' → '+r.to),typeof r==='object'?[r.from,r.to]:[],'pending'));
 for(let c of d.updates?.candidates||[])pendingBox.append(card(c.review_decision?'已判断 · '+c.review_decision+' · 待成功发布':'高价值信号 · Codex 待判断',c.path||c.kind,[],'pending'));box.append(pendingBox);
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
function showPanel(id){panel=id;mode=['recent','delta'].includes(id)?id:'now';panels();view();}
for(let id of ['now','component-view','flow-view','recent','delta'])$(id).onclick=()=>showPanel(id);
$('focus').onclick=()=>{focused=!focused;$('focus').setAttribute('aria-pressed',focused);send()};
$('all-changes').onclick=()=>focusChange(null);
for(let [id,action] of [['zoom-in','in'],['zoom-out','out'],['fit','fit']])$(id).onclick=()=>{if(action==='fit'){focused=false;navigation=null;$('focus').setAttribute('aria-pressed','false')}send({action})};
window.addEventListener('message',e=>{if(!e.data||typeof e.data!=='object')return;
 if(loading&&e.source===loading.contentWindow&&e.data.kind==='lac-ready'){if(loadingKey!==desiredKey()){cancelLoad();return;}clearTimeout(loadTimer);picture.remove();picture=loading;picture.id='picture';picture.style.cssText='';version=loadingKey;loading=null;loadingKey='';$('empty').hidden=true;paint(latest);send({restore:navigation});return;}
 if(e.source!==picture.contentWindow)return;
 if(e.data.kind==='lac-select'){let c=data?.components?.find(c=>nodeId(c.id)===e.data.id);if(c)select(c.id)}
 if(e.data.kind==='lac-navigation'&&e.data.navigation&&typeof e.data.navigation==='object')navigation=e.data.navigation;
});
function paint(next){if(!next)return;data=next;let d=data.development||{},fresh=data.status==='FRESH';
 if(selectedChange!==null&&!(d.changes||[]).some(x=>x.path===selectedChange))selectedChange=null;
 let comparing=version.endsWith(':comparison.html');for(let id of ['zoom-in','zoom-out','fit','focus','all-changes'])$(id).disabled=comparing;
 $('all-changes').setAttribute('aria-pressed',selectedChange===null);
 $('map-caption').textContent=comparing?'这里只显示已接受 IR 的差异；使用图内 Before / Delta / After 控件。':(selectedChange===null?'全部已保存修改':'修改范围：'+selectedChange)+' · 已接受声明的复审范围；工作区候选单独列出。';
 if(selected&&!data.components?.some(c=>c.id===selected)){selected=null;focused=false;$('focus').setAttribute('aria-pressed','false');}
 $('map-title').textContent=version.endsWith(':comparison.html')?'已接受版本 · Before / Delta / After':mode==='recent'?'最近变化 · 仍以已接受版本为准':'已接受的系统蓝图';
 if(version)$('raw').href='/artifact/'+version.replace(':','/');
 $('project-name').textContent=(data.project?.name||'系统')+' · 系统地图';
 $('state').textContent=fresh?'已接受架构 FRESH · 源码证据一致':data.status+' · 保留上次已接受架构';$('status').className='status'+(fresh?'':' stale');
 $('identity').textContent='CONTEXT '+(data.context_hash||'none').slice(0,12)+' / '+(data.revision||'unknown').slice(0,8);
 $('summary').textContent=(data.components||[]).length+' 个已接受组件 · '+(data.relations||[]).length+' 条声明关系。'+(d.observer_running?(d.live?'持续观察已启动。':'只读观察；未启用自动发布。'):'观察线程未运行；保留最后观察。')+' 代码变化先显示，架构语义由 Codex 维护。';
 let reasons=[...(data.reason||[]),...(d.refresh?.reasons||[])];$('reason').textContent=unique(reasons).join(' · ');
 $('scope').textContent=data.coverage?.scope||'仅覆盖配置声明的组件与证据。';$('limits').replaceChildren(...[...(data.coverage?.limitations||[]),...(d.limitations||[])].map(s=>el('li',s)));
 $('graph').textContent=data.graph?.configured?'代码图：'+(data.graph.provider||'external')+' / '+(data.graph.freshness||'unverified'):'CALM 未接入；图中是架构声明，不是完整调用图。';
 $('observation').textContent='OBS '+(d.observation_id||'—').slice(0,12)+' · '+(d.observed_at||'').replace('T',' ').slice(0,19)+' UTC · '+(d.counts?.unmapped||0)+' 项未覆盖';
 let observation=data.context_hash+':'+d.observation_id;if(observation!==lastObservation){lastObservation=observation;$('sources').replaceChildren(...(data.components||[]).map(componentButton));send();}detail();drawProject();drawChanges();drawAnalysis();drawFlows();panels();}
async function poll(){try{let r=await fetch('/api/current',{cache:'no-store',headers:etag?{'If-None-Match':etag}:{}});if(r.status===304)return;if(!r.ok)throw Error(await r.text());let next=await r.json();etag=r.headers.get('ETag')||'';
 if(next.status==='RETRY')throw Error('观察期间状态已变化，保留当前画面，下一次读取重试。');latest=next;view();
}catch(e){etag='';$('state').textContent='暂不可更新 · 保留当前画面';$('reason').textContent=String(e)}finally{setTimeout(poll,1500)}}poll();
</script></html>"""

# The original receipt-bound Archify artifact stays untouched. This local-only
# bridge runs inside its existing opaque sandbox and uses Archify's own camera.
MAP_BRIDGE = r"""<style>
html{color-scheme:light}body{background:#fff!important;padding:0!important;margin:0!important;overflow:hidden!important}.container{margin:0!important;padding:0!important;max-width:none!important;width:100%!important}.toolbar,.header,.footer,.share-chapter,.guided-views,.diagram-guide,.overview-map,.route-probe,.semantic-lens,.node-finder,.focus-chip,.diagram-nav{display:none!important}
.diagram-container{margin:0!important;padding:0!important;border:0!important;box-shadow:none!important;border-radius:0!important;height:100vh!important;min-height:0!important;overflow:hidden!important;display:flex;align-items:center}
.diagram-container>svg{max-height:100vh!important;width:100%!important;height:100%!important}.lac-direct>rect:not(.c-mask){stroke:#b66a13!important;stroke-width:3!important}.lac-dependency>rect:not(.c-mask){stroke:#526fac!important;stroke-width:2.5!important;stroke-dasharray:7 3}.lac-dependent>rect:not(.c-mask){stroke:#267b79!important;stroke-width:2.5!important;stroke-dasharray:2 3}.lac-dependency.lac-dependent>rect:not(.c-mask){stroke:#75578e!important;stroke-dasharray:7 2 2 2}.lac-selected>rect:not(.c-mask){filter:drop-shadow(0 2px 3px #17695855);stroke-width:3!important}.lac-dim{opacity:.22!important}
</style><script>
(()=>{document.documentElement.setAttribute('data-theme','light');document.documentElement.setAttribute('data-embed','true');let svg=document.querySelector('.diagram-container>svg'),nodes=[...document.querySelectorAll('[data-node-id]')];if(!svg)return;svg.parentElement.removeAttribute('data-wide-diagram');let applying=false,timer;
const emit=(kind,more={})=>parent.postMessage({kind,...more},'*');
function navigation(){if(applying)return;clearTimeout(timer);timer=setTimeout(()=>{let v=Archify.view;emit('lac-navigation',{navigation:{scale:v?.state?.().scale,viewport:v?.logicalViewport?.()}})},80)}
for(let n of nodes){n.addEventListener('click',e=>{e.stopImmediatePropagation();emit('lac-select',{id:n.dataset.nodeId})},true);n.addEventListener('keydown',e=>{if(['Enter',' '].includes(e.key)){e.preventDefault();e.stopImmediatePropagation();emit('lac-select',{id:n.dataset.nodeId})}},true)}
window.addEventListener('message',e=>{let m=e.data;if(e.source!==parent||m?.kind!=='lac-map')return;let direct=new Set(m.direct||[]),dependencies=new Set(m.dependencies||[]),dependents=new Set(m.dependents||[]),related=new Set([m.selected]);
for(let p of svg.querySelectorAll('[data-edge-from]'))if([p.dataset.edgeFrom,p.dataset.edgeTo].includes(m.selected)){related.add(p.dataset.edgeFrom);related.add(p.dataset.edgeTo)}
for(let n of nodes){let id=n.dataset.nodeId;n.classList.toggle('lac-direct',direct.has(id));n.classList.toggle('lac-dependency',dependencies.has(id)&&!direct.has(id));n.classList.toggle('lac-dependent',dependents.has(id)&&!direct.has(id));n.classList.toggle('lac-selected',id===m.selected);n.classList.toggle('lac-dim',!!m.focused&&!!m.selected&&!related.has(id))}
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
    quote = (lambda value: "'" + value.replace("'", "''") + "'") if os.name == "nt" else shlex.quote
    entry = Path(archctx.__file__).resolve()
    entry_name = entry.relative_to(repo).as_posix() if entry.is_relative_to(repo) else entry.as_posix()
    config_name = config_path.relative_to(repo).as_posix() if config_path.is_relative_to(repo) else config_path.as_posix()
    prefix = "python " + quote(entry_name) + " --config " + quote(config_name)
    if explicit:
        prefix += " --state-dir " + quote(directory.as_posix())

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
                if url.path == "/analysis-source":
                    if archctx.repo_for(config_path, archctx.load(config_path)) != repo:
                        raise ValueError("repository changed; restart the viewer")
                    self.reply(200, analysis_source(repo, directory, parse_qs(url.query)))
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
                    result["project"] = {"name": repo.name, "root": repo.as_posix(), "config": config_name,
                                         "queries": {"updates": prefix + " updates", "analysis": prefix + " understand --show --details"},
                                         "component_queries": {c["id"]: {command: prefix + " " + command + " " +
                                            ("--from=" if command == "trace" else "--component=") + quote(c["id"])
                                            for command in ("canonical", "evidence", "trace")} for c in result["components"]}}
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
            except (KeyError, IndexError, StopIteration, ValueError, OSError, TypeError) as error:
                self.reply(409, "Blueprint unavailable: " + html.escape(str(error)))
    return Handler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="architecture/architecture.json")
    parser.add_argument("--state-dir")
    parser.add_argument("--port", type=int, default=0, help="0 selects an unused local port")
    parser.add_argument("--no-open", action="store_true")
    parser.add_argument("--live", action="store_true", help="observe saved changes; validate/promote settled shared config/view edits using existing refresh")
    args = parser.parse_args(argv)
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
