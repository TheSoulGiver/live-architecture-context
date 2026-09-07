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


PAGE = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Living Blueprint</title><style>
*{box-sizing:border-box}body{margin:0;background:#f5f6f3;color:#152e31;font:15px system-ui,sans-serif}
header{padding:20px 28px;background:#fff;border-bottom:1px solid #d8e1dd}h1{font-size:22px;margin:0 0 8px}
small{color:#546766}#state{font-weight:600}nav{display:flex;gap:16px;margin:12px 0}a{color:#086b60}
main{display:grid;grid-template-columns:minmax(250px,310px) 1fr;gap:16px;padding:16px}aside{padding:16px;background:#fff;border-radius:8px;max-height:80vh;overflow:auto}
iframe{border:1px solid #d8e1dd;border-radius:8px;width:100%;height:80vh;background:white}details{margin:16px 0}summary{font-weight:600;cursor:pointer}
code{overflow-wrap:anywhere;font-size:12px}p{line-height:1.5}#reason{color:#9a4d00}button{cursor:pointer;padding:6px 12px}li{margin:6px 0}
@media(max-width:800px){main{grid-template-columns:1fr}aside{max-height:35vh}}
</style><header><h1>Living Blueprint</h1><span id="state">Loading accepted architecture…</span>
<div id="reason"></div><small id="identity"></small><nav><button id="now">Current blueprint</button><button id="delta">Before / Delta / After</button><a id="raw" target="_blank" rel="noopener">Open Archify artifact</a></nav></header>
<main><aside><small id="graph"></small><div id="sources"></div><details><summary>Coverage and limitations</summary><p id="scope"></p><ul id="limits"></ul></details></aside><iframe id="picture" title="Archify blueprint" sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox"></iframe></main>
<script>
let selected='current.html',version='',data=null;
const [picture,raw,now,delta,reason,identity,scope,limits,graph,sources]=['picture','raw','now','delta','reason','identity','scope','limits','graph','sources'].map(id=>document.getElementById(id));
function view(){if(!data?.artifacts?.includes(selected))return;let u='/artifact/'+data.generation+'/'+selected;picture.src=u;raw.href=u;}
now.onclick=()=>{selected='current.html';view()};delta.onclick=()=>{selected='comparison.html';view()};
async function poll(){try{let r=await fetch('/api/current',{cache:'no-store'});if(!r.ok)throw Error(await r.text());data=await r.json();
document.querySelector('#state').textContent=data.status+' · '+(data.delta_kind||'no rendered version');reason.textContent=[...(data.reason||[]),...(data.before_reason?[data.before_reason]:[])].join('; ');
delta.textContent=data.before_available?'Before / Delta / After':'Current-only baseline';
identity.textContent='Accepted evidence '+(data.context_hash||'none')+' · checkout '+(data.revision||'unknown');
scope.textContent=data.coverage?.scope||'Declared components and source evidence; coverage is limited to this configuration.';
limits.replaceChildren(...(data.coverage?.limitations||[]).map(s=>{let e=document.createElement('li');e.textContent=s;return e}));
graph.textContent=data.graph?.configured?'Code facts: '+(data.graph.provider||'external')+' · '+(data.graph.freshness||'unverified'):'Code graph: not connected';
if(data.generation!==version){version=data.generation;sources.replaceChildren();for(let c of data.components||[]){let d=document.createElement('details'),s=document.createElement('summary');s.textContent=c.name||c.id;d.append(s);let p=document.createElement('p');p.textContent=c.purpose||'';d.append(p);for(let [i,e] of (c.evidence||[]).entries()){let a=document.createElement('a');a.textContent=e.path+':'+e.line;a.target='_blank';a.rel='noopener';a.href='/source?component='+encodeURIComponent(c.id)+'&item='+i+'&context='+encodeURIComponent(data.context_hash);let row=document.createElement('p');row.append(a);d.append(row)}sources.append(d)}view()}
}catch(e){document.querySelector('#state').textContent='UNAVAILABLE · retained view';reason.textContent=String(e)}finally{setTimeout(poll,1500)}}poll();
</script></html>"""


def handler(config_path: Path, explicit: str | None):
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

        def reply(self, code: int, value: str | bytes, kind: str = "text/html; charset=utf-8"):
            body = value.encode("utf-8") if isinstance(value, str) else value
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
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
                value = archctx.snapshot(config_path, explicit)
                receipt = value.get("archify", {})
                if url.path == "/api/current":
                    result = {key: value.get(key) for key in ("status", "revision", "reason", "graph")}
                    result.update(context_hash=value.get("last_good_context_hash"), generation=Path(receipt.get("generation", "")).name,
                                  artifacts=list(receipt.get("artifacts", {})), delta_kind=receipt.get("delta_kind"),
                                  before_reason=receipt.get("before_reason"), before_available=receipt.get("before_available", False),
                                  coverage=value.get("context", {}).get("coverage"), components=value.get("context", {}).get("components", []))
                    self.reply(200, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")
                    return
                if url.path.startswith("/artifact/"):
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
                    self.reply(200, path.read_bytes(), mimetypes.guess_type(path.name)[0] or "application/octet-stream")
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
    args = parser.parse_args()
    path = Path(args.config).resolve()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler(path, args.state_dir))
    url = f"http://127.0.0.1:{server.server_port}/"
    print(json.dumps({"url": url, "mode": "read_only", "next_action": "keep the repository watcher running for accepted updates"}), flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
