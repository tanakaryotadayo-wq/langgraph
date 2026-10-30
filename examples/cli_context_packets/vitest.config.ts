import { defineConfig } from 'vitest/config';
import path from 'node:path';
const root = import.meta.dirname;
export default defineConfig({
  plugins:[{name:'upstream-host-ports',enforce:'pre',resolveId(id,importer){
    if(!importer?.includes('/packets/'))return;
    const p=path.resolve(path.dirname(importer),id);
    const map:{[suffix:string]:string}={
      '/utils/debugLogger.js':'logging.ts',
      '/utils/shell-utils.js':'shell.ts',
      '/utils/fileUtils.js':'files.ts',
    };
    for(const [suffix,file] of Object.entries(map))if(p.endsWith(suffix))return path.join(root,'bridges',file);
  }}],
  test:{pool:'forks',maxWorkers:2,minWorkers:1,
    include:[
      'packets/qwen/source/packages/core/src/utils/gitIgnoreParser.test.ts',
      'packets/qwen/source/packages/core/src/utils/qwenIgnoreParser.test.ts',
      'packets/qwen/source/packages/core/src/utils/workspaceContext.test.ts',
      'packets/qwen/source/packages/core/src/utils/ripgrepUtils.test.ts',
      'packets/continue/source/core/context/retrieval/utils.vitest.ts',
    ],
  },
});
