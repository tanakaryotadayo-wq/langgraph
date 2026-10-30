// SPDX-License-Identifier: Apache-2.0
import * as esbuild from 'esbuild';
import path from 'node:path';
import fs from 'node:fs';
const root = path.resolve(import.meta.dirname, '..');
const cont = path.join(root, 'bridges/continue-host.ts');
const replacements = {
  '/utils/debugLogger.js': path.join(root, 'bridges/logging.ts'),
  '/utils/shell-utils.js': path.join(root, 'bridges/shell.ts'),
  '/utils/fileUtils.js': path.join(root, 'bridges/files.ts'),
  '/autocomplete/util/openedFilesLruCache': cont,
  '/llm/countTokens.js': path.join(root,'bridges/tokenizer.ts'),
  '/indexing/LanceDbIndex': cont,
  '/indexing/refreshIndex': cont,
  '/indexing/sqlite': cont,
  '/indexing/chunk/ChunkCodebaseIndex': cont,
  '/config/util': cont,
  '/util/generateRepoMap': cont,
  '/util/ideUtils': cont,
  '/util/messageContent': cont,
  '/tools/callTool': cont,
};
const applied = new Set();
await esbuild.build({
  entryPoints: [path.join(root,'bridges/worker.ts')],
  outfile: path.join(root,'dist/worker.mjs'), bundle:true, platform:'node', format:'esm', target:'node24',
  packages:'external', sourcemap:true, metafile:true,
  define:{__dirname:JSON.stringify(path.join(root,'dist'))},
  plugins: [{name:'explicit-runtime-seams',setup(build) {
    build.onResolve({filter:/.*/}, args => {
      if (!args.importer.includes('/packets/')) return;
      const resolved = path.resolve(path.dirname(args.importer), args.path);
      for (const [suffix, replacement] of Object.entries(replacements)) {
        if (resolved.endsWith(suffix)) { applied.add(resolved.slice(root.length+1)); return {path:replacement}; }
      }
    });
  }}],
}).then(async result => {
  await fs.promises.cp(path.join(root,'node_modules/tree-sitter-wasms/out'),path.join(root,'dist/tree-sitter-wasms'),{recursive:true});
  const thirdParty=[];
  for(const [name,license] of [['aider','LICENSE.txt'],['qwen','LICENSE'],['continue','LICENSE'],['codex','LICENSE']])
    thirdParty.push(name+'\n'+fs.readFileSync(path.join(root,'packets',name,'source',license),'utf8'));
  const tokenizerSource=fs.readFileSync(path.join(root,'packets/continue/source/core/llm/llamaTokenizer.js'),'utf8');
  thirdParty.push('Continue vendored Llama tokenizer\n'+tokenizerSource.slice(0,tokenizerSource.indexOf('*/')+2));
  for(const pkg of ['web-tree-sitter','tree-sitter-wasms'])
    for(const license of ['LICENSE','LICENSE.txt','LICENSE.md']){
      const file=path.join(root,'node_modules',pkg,license);
      if(fs.existsSync(file))thirdParty.push(pkg+'\n'+fs.readFileSync(file,'utf8'));
    }
  fs.writeFileSync(path.join(root,'dist/THIRD_PARTY_NOTICES.txt'),thirdParty.join('\n\n'));
  fs.writeFileSync(path.join(root,'dist/build-manifest.json'), JSON.stringify({
  runtimeSeams:[...applied].sort(), inputs:Object.keys(result.metafile.inputs).sort(),
},null,2)+'\n');
});
