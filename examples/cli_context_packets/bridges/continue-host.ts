// SPDX-License-Identifier: Apache-2.0
// Adapt Continue's IDE/storage/model interfaces to the supplied workspace snapshot.
import { DatabaseSync } from 'node:sqlite';
import { tagToString } from '../packets/continue/source/core/indexing/utils';
import { RETRIEVAL_PARAMS } from '../packets/continue/source/core/util/parameters';
export let request: any;
export let modelCall: (params: any) => Promise<any>;
let readTool: (name:string,args:any) => Promise<any>;
let db: DatabaseSync;
export const openedFilesLruCache = new Map<string, true>();
export function configure(input: any, call: typeof modelCall, tool: typeof readTool) {
  request = input; modelCall = call; readTool=tool;
  if(input.bm25_threshold!==undefined) RETRIEVAL_PARAMS.bm25Threshold=input.bm25_threshold;
  openedFilesLruCache.clear();
  for (const filepath of input.recent_files ?? []) openedFilesLruCache.set(filepath, true);
  db = new DatabaseSync(':memory:');
  db.exec(`CREATE VIRTUAL TABLE fts USING fts5(path, content, tokenize='trigram');
    CREATE TABLE fts_metadata(id INTEGER PRIMARY KEY,path TEXT,cacheKey TEXT,chunkId INTEGER);
    CREATE TABLE chunks(id INTEGER PRIMARY KEY,path TEXT,content TEXT,cacheKey TEXT,startLine INTEGER,endLine INTEGER,"index" INTEGER);
    CREATE TABLE chunk_tags(chunkId INTEGER,tag TEXT);`);
  const tag = tagToString({ directory: input.root_uri, branch: input.branch ?? 'workspace', artifactId: 'chunks' });
  for (const [i, c] of (input.chunks ?? []).entries()) {
    const id = i + 1;
    db.prepare('INSERT INTO chunks VALUES(?,?,?,?,?,?,?)').run(id,c.filepath,c.content,c.digest,c.startLine,c.endLine,c.index ?? i);
    db.prepare('INSERT INTO fts(rowid,path,content) VALUES(?,?,?)').run(id,c.filepath,c.content);
    db.prepare('INSERT INTO fts_metadata VALUES(?,?,?,?)').run(id,c.filepath,c.digest,id);
    db.prepare('INSERT INTO chunk_tags VALUES(?,?)').run(id,tag);
  }
}
export const SqliteDb = { get: async () => ({
  exec: async (sql: string) => db.exec(sql),
  all: async (sql: string, args: any[] = []) => db.prepare(sql).all(...args),
  get: async (sql: string, args: any[] = []) => db.prepare(sql).get(...args),
  run: async (sql: string, args: any[] = []) => {
    const result = db.prepare(sql).run(...args);
    return { changes: Number(result.changes), lastID: Number(result.lastInsertRowid) };
  },
}) };
export const ChunkCodebaseIndex = { artifactId: 'chunks' };
export const IndexResultType = { Compute: 'compute', AddTag: 'addTag', RemoveTag: 'removeTag', Delete: 'delete' };
export const LanceDbIndex = { create: async () => ({
  retrieve: async (query: string, n: number) => {
    // Local stdio embedding provider, or caller-supplied scored chunks.
    if (request.semantic_chunks) return request.semantic_chunks.slice(0, Math.ceil(n));
    const vectors = await modelCall({operation:'embed',texts:[query,...request.chunks.map((c:any)=>c.content)]});
    if(!Array.isArray(vectors) || vectors.length!==request.chunks.length+1)
      throw new Error('Embedding provider must return one vector per input text');
    const dimension=vectors[0]?.length;
    if(!dimension || vectors.some((v:any)=>!Array.isArray(v)||v.length!==dimension||v.some((x:any)=>typeof x!=='number'||!Number.isFinite(x))))
      throw new Error('Invalid embedding dimensions/values');
    const queryVector=vectors[0];
    const norm=(v:number[])=>Math.sqrt(v.reduce((sum,x)=>sum+x*x,0));
    if(vectors.some((v:number[])=>norm(v)===0))throw new Error('Zero-length embedding');
    return request.chunks.map((chunk:any,i:number)=>({chunk,score:
      vectors[i+1].reduce((sum:number,x:number,j:number)=>sum+x*queryVector[j],0)/(norm(queryVector)*norm(vectors[i+1]))}))
      .sort((a:any,b:any)=>b.score-a.score).slice(0,Math.ceil(n)).map((r:any)=>r.chunk);
  },
}) };
export const getModelByRole = (config: any, role: string) => config.selectedModelByRole[role];
export default async function generateRepoMap() { return request.repo_map ?? ''; }
export const renderChatMessage = (message: any) => typeof message.content === 'string'
  ? message.content : message.content.map((p: any) => p.text ?? '').join('');
export const resolveRelativePathInDir = async (filepath: string, ide: any) => {
  const roots = await ide.getWorkspaceDirs();
  const uri = new URL(filepath, roots[0].replace(/\/?$/, '/')).href;
  if (!uri.startsWith(roots[0].replace(/\/?$/, '/'))) throw new Error('Path leaves workspace');
  await ide.readFile(uri);
  return uri;
};
export async function callBuiltInTool(name: string, args: any) {
  return await readTool(name,args);
}
