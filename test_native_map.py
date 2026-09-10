"""Exercise the native map's real page script with bounded product fixtures."""
import shutil
import subprocess
import unittest

import archctx_blueprint as blueprint


class NativeMapTest(unittest.TestCase):
    def test_component_identity_analysis_freshness_and_native_navigation(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("Node is needed for the real page script check")
        script = blueprint.PAGE.split("<script>", 1)[1].split("</script>", 1)[0]
        harness = r"""
const assert=require('node:assert/strict'),vm=require('node:vm');
class Element {
 constructor(tag='div'){this.tag=tag;this.children=[];this.dataset={};this.style={};this.textContent='';this.contentWindow={postMessage(){}}}
 set innerHTML(_){throw Error('Product data must use textContent')}
 append(...items){this.children.push(...items)} replaceChildren(...items){this.children=items}
 insertBefore(item,before){let i=this.children.indexOf(before);this.children.splice(i<0?this.children.length:i,0,item)}
 querySelector(tag){return this.children.find(e=>e.tag===tag)||null}
 setAttribute(key,value){this[key]=value} removeAttribute(key){delete this[key]}
 cloneNode(){return new Element(this.tag)} remove(){}
}
const elements={},get=id=>elements[id]??=new Element(),text=e=>[e.textContent,...e.children.map(text)].join('\n'),
 all=e=>[e,...e.children.flatMap(all)];
const fixture={status:'FRESH',context_hash:'accepted',generation:'accepted',artifacts:['current.html'],
 project:{name:'Native project',root:'D:/Native project',component_queries:{service:{canonical:"python archctx.py --config 'architecture/map.json' canonical --component='service'",evidence:"python archctx.py --config 'architecture/map.json' evidence --component='service'",trace:"python archctx.py --config 'architecture/map.json' trace --from='service'"}},queries:{updates:'python archctx.py updates',analysis:'python archctx.py understand --show --details'}},
 components:[{id:'service',name:'<img src=x onerror=alert(1)>',purpose:'Service purpose',evidence:[{path:'service.py',line:3}]},{id:'storage',name:'Storage',evidence:[]}],
 relations:[{id:'service-store',from:'service',to:'storage',kind:'writes',dependency:'from_to',evidence:[{path:'service.py',line:3}]}],
 development:{observation_id:'obs-1',observer_running:true,changes:[],updates:{source_analysis:{configured:true,status:'FRESH',analysis_id:'analysis-1',candidate_count:3,
 provider:{name:'Provider diagnostic only',revision:'provider-revision'},source_files:['service.py'],changed_files:[],
 candidates:[{id:'ua:stable',title:'Related discovery',summary:'<script>alert(1)</script>',files:['service.py'],related_components:['service'],review_state:'unreviewed',content_revision:'content-1',evidence_revision:'evidence-1',evidence:[{path:'service.py',line:3,sha256:'hash-1'}]},
 {id:'ua:reviewed',title:'Reviewed source finding',files:['service.py'],related_components:['service'],review_state:'accepted',bindings:['component:service'],content_revision:'content-2',evidence_revision:'evidence-2'},
 {id:'ua:layer-only',title:'service',summary:'Unrelated semantic layer',related_components:[],review_state:'unreviewed'}],tour:[{order:1,title:'Discovered tour',description:'Investigate service'}]}}}};
const context=vm.createContext({TextEncoder,fixture,document:{getElementById:get,createElement:tag=>new Element(tag)},window:{addEventListener(){}},
 fetch:()=>new Promise(()=>{}),setTimeout(){},clearTimeout(){}}),read=code=>vm.runInContext(code,context);
read(require('node:fs').readFileSync(0,'utf8'));read("latest=fixture;version='accepted:current.html';paint(latest)");
assert.ok(text(get('project')).includes('已确认组件'));assert.equal(get('project').hidden,false);
get('component-view').onclick();read("select('service')");
assert.equal(get('detail').dataset.component,'service');assert.ok(text(get('detail')).includes('已确认组件'));
assert.ok(text(get('detail')).includes("canonical --component='service'"));
assert.ok(text(get('detail')).includes("evidence --component='service'"));
assert.ok(text(get('detail')).includes('<img src=x onerror=alert(1)>'));
assert.ok(text(get('analysis')).includes('<script>alert(1)</script>'));
assert.ok(text(get('analysis')).includes('Related discovery'));assert.ok(text(get('analysis')).includes('已复审绑定：component:service'));
assert.equal(text(get('analysis')).includes('Unrelated semantic layer'),false);
assert.equal(all(get('analysis')).some(e=>e.tag==='script'),false);
assert.ok(all(get('analysis')).some(e=>e.href==='/analysis-source?analysis=analysis-1&path=service.py&sha=hash-1&line=3'));
assert.ok(all(get('detail')).some(e=>e.href==='/source?component=service&item=0&context=accepted'));
assert.ok(get('analysis').children.find(e=>e.tag==='details'&&text(e).includes('分析来源与覆盖边界')));
assert.equal(get('analysis').children.some(e=>e.textContent==='Provider diagnostic only'),false);
read("fixture.development.observation_id='obs-2';fixture.development.updates.source_analysis.status='STALE';fixture.development.updates.source_analysis.changed_files=['service.py'];paint(fixture)");
assert.ok(text(get('analysis')).includes('源码已变化 · 待重新理解'));assert.ok(get('state').textContent.includes('已接受架构 FRESH'));
assert.equal(get('detail').dataset.component,'service');
get('flow-view').onclick();assert.equal(get('flows').hidden,false);assert.equal(get('analysis').hidden,true);
assert.ok(text(get('flows')).includes('service 依赖 storage'));assert.ok(text(get('flows')).includes('service.py:3'));
assert.ok(text(get('flows')).includes('已发现的源码导览 · 待核对流程'));
get('recent').onclick();assert.equal(get('changes').hidden,false);assert.ok(text(get('changes')).includes('工作区的变化'));
get('now').onclick();assert.equal(get('project').hidden,false);assert.equal(get('flows').hidden,true);
assert.equal(get('now')['aria-pressed'],true);
get('project').children[0].children[0].onclick();assert.equal(get('detail').dataset.component,'');
assert.equal(text(get('detail')).includes('Service purpose'),false);
console.log('PASS native map: exact component identity, accepted/discovered freshness, queries, escaped text, source links and navigation');
"""
        result = subprocess.run([node, "-e", harness], input=script, text=True,
                                encoding="utf-8", capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
