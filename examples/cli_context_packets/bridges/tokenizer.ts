// SPDX-License-Identifier: Apache-2.0
// Keep Continue's original default llama2 tokenizer, with an in-process async
// boundary instead of the editor's workerpool file layout.
import llamaTokenizer from '../packets/continue/source/core/llm/llamaTokenizer.js';
export async function countTokensAsync(text: string) { return llamaTokenizer.encode(text ?? '').length; }
