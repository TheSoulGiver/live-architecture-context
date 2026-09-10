#!/usr/bin/env node
// Packaged dependency evidence from the pinned provider; no parser lives here.
import { existsSync, readFileSync, realpathSync, statSync, writeFileSync } from 'node:fs';
import { builtinModules } from 'node:module';
import { basename, isAbsolute, join, relative, resolve, sep } from 'node:path';
import { spawnSync } from 'node:child_process';
import { pathToFileURL } from 'node:url';

const LIMIT = 4 * 1024 * 1024;
const JS = new Set(['typescript', 'javascript', 'tsx', 'jsx', 'vue']);
const CONFIGS = new Set(['tsconfig.json', 'go.mod', 'composer.json', 'Package.swift']);
const sorted = values => [...new Set(values)].sort();

function json(path) {
  if (statSync(path).size > 2 * LIMIT) throw new Error('dependency JSON exceeds 8 MiB');
  return JSON.parse(readFileSync(path, 'utf8'));
}

function sourcePath(path) {
  if (typeof path !== 'string' || !path || path.length > 1024 || isAbsolute(path)
      || path.includes('\\') || path.split('/').some(p => !p || ['.', '..', '.git', '.ua', '.archctx'].includes(p))) {
    throw new Error('dependency inventory needs normalized repository paths');
  }
  return path;
}

async function main() {
  const [pluginArg, inputPath, outputPath] = process.argv.slice(2);
  if (!pluginArg || !inputPath || !outputPath) {
    throw new Error('usage: node analysis_dependencies.mjs <plugin-root> <input.json> <output.json>');
  }
  const input = json(inputPath);
  if (!Array.isArray(input.files) || !input.files.length || input.files.length > 20000
      || !Array.isArray(input.analysisPaths) || !input.analysisPaths.length || input.analysisPaths.length > 64
      || input.externalModules !== undefined && (!Array.isArray(input.externalModules)
        || input.externalModules.length > 1000 || input.externalModules.some(p => typeof p !== 'string'))) {
    throw new Error('dependency extraction needs bounded inventory, selected paths and external module names');
  }
  const root = realpathSync(input.projectRoot);
  const plugin = realpathSync(pluginArg);
  const core = await import(pathToFileURL(join(plugin, 'packages/core/dist/index.js')).href);
  const resolver = await import(pathToFileURL(join(plugin, 'skills/understand/extract-import-map.mjs')).href);
  const { analyzeFileWithOutcomes } = await import(pathToFileURL(join(plugin, 'skills/understand/extract-structure-result.mjs')).href);
  const codeLanguages = new Set(core.builtinLanguageConfigs.filter(c => c.treeSitter).map(c => c.id));
  const files = input.files.map(file => ({ ...file, path: sourcePath(file.path),
    fileCategory: file.fileCategory || (codeLanguages.has(file.language) ? 'code' : 'other') }));
  const byPath = new Map(files.map(file => [file.path, file]));
  const selected = input.analysisPaths.map(sourcePath);
  if (byPath.size !== files.length || new Set(selected).size !== selected.length
      || selected.some(path => !byPath.has(path))) throw new Error('duplicate or unlisted dependency source');
  let bytes = 0;
  const contents = new Map();
  // The provider reads selected source and resolver configuration, never other inventory source.
  for (const path of sorted([...selected, ...files.filter(f => CONFIGS.has(basename(f.path))
    && existsSync(join(root, f.path))).map(f => f.path)])) {
    const actual = realpathSync(join(root, path));
    const inside = relative(root, actual);
    if (isAbsolute(inside) || inside === '..' || inside.startsWith(`..${sep}`)) throw new Error('dependency source escapes capture');
    bytes += statSync(actual).size;
    if (bytes > LIMIT) throw new Error('dependency source and resolver configuration exceed 4 MiB');
    if (selected.includes(path)) contents.set(path, readFileSync(actual, 'utf8'));
  }
  const resolverInput = `${outputPath}.resolver-input.json`;
  const resolverOutput = `${outputPath}.resolver-output.json`;
  writeFileSync(resolverInput, JSON.stringify({ projectRoot: root, files, analysisPaths: selected }));
  const run = spawnSync(process.execPath, [join(plugin, 'skills/understand/extract-import-map.mjs'),
    resolve(resolverInput), resolve(resolverOutput)], {
    cwd: root, encoding: 'utf8', timeout: 120000, maxBuffer: LIMIT,
    env: { ...process.env, UNDERSTAND_NO_WORKTREE_REDIRECT: '1' }, windowsHide: true,
  });
  if (run.error || run.status !== 0) throw new Error(`upstream import resolver failed: ${run.error?.message || run.stderr?.slice(-1200)}`);
  const mapped = json(resolverOutput);
  if (!mapped.scriptCompleted || !mapped.importMap || mapped.failures?.length) {
    throw new Error('upstream import resolution incomplete; inspect retained resolver output');
  }
  const tree = new core.TreeSitterPlugin(core.builtinLanguageConfigs.filter(c => c.treeSitter));
  await tree.init();
  const registry = new core.PluginRegistry();
  registry.register(tree);
  core.registerAllParsers(registry);
  const fileSet = new Set(byPath.keys());
  const externalPython = new Set(input.externalModules || []);
  const externalNode = new Set(builtinModules.flatMap(name => [name, `node:${name}`]));
  const output = { version: 1, scriptCompleted: true, files: {}, failures: [], stats: mapped.stats,
    limitations: ['Static imports only; dynamic loading and runtime reachability remain unknown.',
      'Resolved paths are inventory evidence; the caller must capture and verify dependency bytes.'] };
  for (const path of selected) {
    const file = byPath.get(path);
    const dependencies = mapped.importMap[path];
    if (!Array.isArray(dependencies) || dependencies.some(p => typeof p !== 'string' || !fileSet.has(p))) {
      throw new Error('upstream dependency output contains an invalid repository path');
    }
    const row = { dependencies: sorted(dependencies), unresolvedLocal: [], unknown: [], external: [], coverage: 'resolved' };
    const { analysis, structureOutcome } = analyzeFileWithOutcomes(registry, file, contents.get(path));
    if (structureOutcome !== 'succeeded') {
      row.coverage = 'unsupported';
      row.unknown.push(`structural extraction ${structureOutcome}: ${file.language || 'unknown language'}`);
      output.failures.push({ path, stage: 'raw-imports', message: row.unknown[0] });
    } else {
      for (const imp of analysis.imports) {
        let matches = [];
        if (file.language === 'python') {
          matches = resolver.resolvePythonImport(imp.source, imp.specifiers, file, { fileSet });
        } else if (JS.has(file.language) && imp.source.startsWith('.')) {
          const target = resolver.resolveTsJsImport(imp.source, file, { fileSet, tsConfigs: new Map() });
          if (target) matches = [target];
        }
        if (matches.length) row.dependencies.push(...matches);
        else if (imp.source.startsWith('.') && (file.language === 'python' || JS.has(file.language))) row.unresolvedLocal.push(imp.source);
        else if (file.language === 'python' && externalPython.has(imp.source.split('.')[0])
          || JS.has(file.language) && externalNode.has(imp.source)) row.external.push(imp.source);
        // ponytail: retain ambiguity for other import forms; use an upstream per-import resolver API when available.
        else row.unknown.push(imp.source);
      }
      if (row.unresolvedLocal.length || row.unknown.length) row.coverage = 'partial';
    }
    for (const key of ['dependencies', 'unresolvedLocal', 'unknown', 'external']) row[key] = sorted(row[key]);
    output.files[path] = row;
  }
  writeFileSync(outputPath, `${JSON.stringify(output, null, 2)}\n`);
}

try { await main(); }
catch (error) { process.stderr.write(`${error.message}\n`); process.exitCode = 1; }
