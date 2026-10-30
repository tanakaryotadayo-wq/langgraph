// SPDX-License-Identifier: Apache-2.0
import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { createHash } from 'node:crypto';
import { createInterface } from 'node:readline';
import { runRipgrep } from '../packets/qwen/source/packages/core/src/utils/ripgrepUtils.js';
import { searchGlobDirectory } from '../packets/qwen/source/packages/core/src/tools/glob-search.js';
import { FileDiscoveryService } from '../packets/qwen/source/packages/core/src/services/fileDiscoveryService.js';
import { readFileHandleBytes } from '../packets/qwen/source/packages/core/src/utils/file-read-source.js';
import NoRerankerRetrievalPipeline from '../packets/continue/source/core/context/retrieval/pipelines/NoRerankerRetrievalPipeline';
import RerankerRetrievalPipeline from '../packets/continue/source/core/context/retrieval/pipelines/RerankerRetrievalPipeline';
import { configure, modelCall } from './continue-host';
import { chunkDocument } from '../packets/continue/source/core/indexing/chunk/chunk.js';
import { getParserForFile } from '../packets/continue/source/core/util/treeSitter.js';

console.log = console.info = console.warn = (...args: unknown[]) => console.error(...args);
const write = (value: any) => process.stdout.write(JSON.stringify(value)+'\n');
const pending = new Map<number, {resolve:(v:any)=>void,reject:(e:any)=>void}>();
let sequence = 0;
function callModel(params: any): Promise<any> {
  const id = ++sequence;
  return new Promise((resolve,reject) => {
    pending.set(id,{resolve,reject}); write({type:'model_request',id,params});
  });
}
async function collect(input: any) {
  const root = await fs.realpath(input.root);
  const service = new FileDiscoveryService(root);
  const abort = AbortSignal.timeout(input.timeout_ms ?? 30000);
  const discovered = await searchGlobDirectory({searchDir:root, projectRoot:root,
    containmentRoot:root, pattern:input.glob ?? '**/*', entryLimit:input.max_files ?? 500,
    fileFilteringOptions:{respectGitIgnore:true,respectQwenIgnore:true}},service,abort);
  const filenames = discovered.entries.map(e=>e.fullpath());
  const safe:string[] = [];
  const omitted:any[]=[];
  for (const p of filenames) {
    const real = await fs.realpath(p);
    if (real !== root && !real.startsWith(root+path.sep)) {omitted.push({file:p,reason:'outside_workspace'});continue;}
    if ((await fs.stat(real)).isFile()) safe.push(real);
  }
  if(input.discover_only)return {files:safe,omitted,hit_limit:discovered.hitLimit};
  const hits = new Map<string,number[]>();
  let recovery: any;
  if (safe.length && input.pattern) {
    const result = await runRipgrep(['--json','--line-number','--max-count',String(input.max_hits_per_file ?? 20),'-e',input.pattern,'--',...safe],abort,false);
    if (result.error || result.incomplete) throw result.error ?? new Error('Incomplete ripgrep result');
    recovery=result.recovery;
    for (const line of result.stdout.split('\n').filter(Boolean)) {
      const event=JSON.parse(line);
      if(event.type==='match') {
        const file=event.data.path.text;
        if(!file) throw new Error('Non-UTF8 path cannot be represented');
        const lines=hits.get(file)??[]; lines.push(event.data.line_number); hits.set(file,lines);
      }
    }
  } else for(const file of safe) hits.set(file,[1]);
  const chunks:any[]=[];
  for(const [file, positions] of hits) {
    const handle=await fs.open(file,'r');
    try {
      const stat=await handle.stat();
      if(stat.size>(input.max_file_bytes??2_000_000)){omitted.push({file,reason:'max_file_bytes',bytes:stat.size});continue;}
      const bytes=await readFileHandleBytes(handle,stat.size,abort);
      const after=await handle.stat();
      if(stat.size!==after.size || stat.mtimeMs!==after.mtimeMs) throw new Error('File changed during collection: '+file);
      if(bytes.includes(0)){omitted.push({file,reason:'binary'});continue;}
      let text:string;
      try{text=new TextDecoder('utf-8',{fatal:true,ignoreBOM:true}).decode(bytes);}
      catch{omitted.push({file,reason:'non_utf8'});continue;}
      const lines=text.split('\n'); const digest=createHash('sha256').update(bytes).digest('hex');
      const ranges:Array<[number,number]>=[];
      for(const line of positions) {
        const start=Math.max(0,line-1-(input.context_lines??12));
        const end=Math.min(lines.length,line+(input.context_lines??12));
        const last=ranges.at(-1);
        if(last && start<=last[1]) last[1]=Math.max(last[1],end); else ranges.push([start,end]);
      }
      for(const [index,[start,end]] of ranges.entries()) chunks.push({filepath:pathToFileURL(file).href,
        content:lines.slice(start,end).join('\n'),startLine:start,endLine:end-1,digest,index});
    } finally {await handle.close();}
  }
  return {chunks,files:safe,omitted,hit_limit:discovered.hitLimit,recovery};
}
async function retrieve(input:any) {
  const toolOutputs:any[]=[];
  const runReadTool=async(name:string,args:any)=>{
    const root=await fs.realpath(fileURLToPath(input.root_uri));
    const checked=async(p:string)=>{
      const file=await fs.realpath(p.startsWith('file:')?fileURLToPath(p):path.resolve(root,p));
      if(file!==root&&!file.startsWith(root+path.sep))throw new Error('Tool path leaves workspace');
      return file;
    };
    let items:any[];
    if(name==='read_file'){
      const file=await checked(args.filepath);
      const stat=await fs.stat(file);
      if(stat.size>(input.max_file_bytes??2_000_000))throw new Error('Tool file exceeds max_file_bytes');
      items=[{name:file,content:await fs.readFile(file,'utf8'),uri:{type:'file',value:pathToFileURL(file).href}}];
    }else if(name==='grep_search'){
      const r=await collect({root,pattern:args.query,context_lines:12,max_files:input.max_files??500});
      items=r.chunks.map((c:any)=>({name:c.filepath,content:c.content,uri:{type:'file',value:c.filepath}}));
    }else if(name==='file_glob_search'){
      const r=await collect({root,glob:args.pattern,discover_only:true,max_files:input.max_files??500});
      items=[{name:'file_glob_search',content:r.files.map((f:string)=>path.relative(root,f)).join('\n')}];
    }else if(name==='ls'||name==='view_subdirectory'){
      const dir=await checked(args.dirPath??args.directory_path??'.');
      const entries=await fs.readdir(dir,{withFileTypes:true});
      items=[{name,content:entries.map(e=>e.name+(e.isDirectory()?'/':'')).sort().join('\n')}];
    }else if(name==='view_repo_map'){
      items=[{name,content:input.repo_map??''}];
    }else throw new Error('Unsupported read tool: '+name);
    toolOutputs.push({name,args,items});
    return items;
  };
  configure(input,callModel,runReadTool);
  const readDocuments=new Map<string,any>();
  const llm={ title:input.model_enabled?'local-stdio':'local-disabled', contextLength:input.context_length??32768,
    chat:async(messages:any[])=>({role:'assistant',content:await callModel({operation:'chat',messages:[...(input.history??[]),...messages]})}) };
  const selectedModelByRole:any={};
  if(input.repo_map && input.model_enabled) selectedModelByRole.repoMapFileSelection=llm;
  if(input.use_embeddings) selectedModelByRole.embed={maxEmbeddingChunkSize:384};
  if(input.rerank) selectedModelByRole.rerank={rerank:async(query:string,chunks:any[])=>{
    const scores=await callModel({operation:'rerank',query,chunks});
    if(!Array.isArray(scores)||scores.length!==chunks.length||scores.some(x=>typeof x!=='number'||!Number.isFinite(x)))
      throw new Error('Rerank must return one finite score per candidate');
    return scores;
  }};
  const options={input:input.query,nFinal:input.n_final??25,nRetrieve:input.n_retrieve??50,
    tags:[{directory:input.root_uri,branch:input.branch??'workspace'}],filterDirectory:input.root_uri,
    config:{selectedModelByRole,experimental:{codebaseToolCallingOnly:!!input.tool_calling_only,
      modelRoles:{repoMapFileSelection:!!input.model_enabled}}},llm,
    ide:{getWorkspaceDirs:async()=>[input.root_uri],getOpenFiles:async()=>input.open_files??[],
      readFile:async(uri:string)=>{
        const file=await fs.realpath(fileURLToPath(uri)); const root=await fs.realpath(fileURLToPath(input.root_uri));
        if(file!==root && !file.startsWith(root+path.sep))throw new Error('IDE read leaves workspace');
        const stat=await fs.stat(file);
        if(stat.size>(input.max_file_bytes??2_000_000))throw new Error('IDE read exceeds max_file_bytes');
        const content=await fs.readFile(file,'utf8');
        readDocuments.set(uri,{filepath:uri,content,startLine:0,endLine:content.split('\n').length-1,
          digest:createHash('sha256').update(content).digest('hex'),index:0});
        return content;
      }},
  };
  const pipeline=input.rerank?new RerankerRetrievalPipeline(options as any):new NoRerankerRetrievalPipeline(options as any);
  const chunks=await pipeline.run({query:input.query,tags:options.tags,filterDirectory:input.root_uri,includeEmbeddings:!!input.use_embeddings});
  for(const c of chunks)if(c.digest===c.filepath)c.derivation='continue.chunkDocument';
  return {chunks,source_documents:[...readDocuments.values()],tool_outputs:toolOutputs,backend:'continue-upstream-pipeline/sqlite-fts5/local-stdio',
    bm25_threshold:input.bm25_threshold??-2.5};
}
const reader=createInterface({input:process.stdin});
reader.on('line',line=>{
  let message:any;
  try{message=JSON.parse(line);}catch(error){write({type:'error',error:String(error)});return;}
  if(message.type==='model_response') {
    const callback=pending.get(message.id);pending.delete(message.id);
    if(message.error)callback?.reject(new Error(message.error));else callback?.resolve(message.result);
    return;
  }
  const operation=message.operation==='qwen'?collect:message.operation==='continue'?retrieve:
    message.operation==='continue_chunk'?async(input:any)=>{
      const chunks=[];
      for await(const c of chunkDocument(input))chunks.push(c);
      const parser=await getParserForFile(input.filepath);
      return {chunks,parser_available:parser!==undefined};
    }:undefined;
  if(!operation){write({type:'error',error:'Unknown operation'});return;}
  operation(message).then(result=>write({type:'result',result})).catch(error=>write({type:'error',error:String(error?.stack??error)}));
});
