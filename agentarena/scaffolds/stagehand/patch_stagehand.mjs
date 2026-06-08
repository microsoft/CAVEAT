// Patch Stagehand's LLMProvider to use the OpenAI *chat-completions* API instead
// of the default Responses API. The Responses API sends encrypted reasoning
// content that non-OpenAI TRAPI models (Kimi, Llama) reject with
// "Encrypted content is not supported with this model". Chat-completions works
// for all of them (incl. gpt-5.5/4.1/4o). Idempotent; re-run after npm install.
import fs from "fs";
const ORIG = "        model = provider(subModelName);";
const NEW  = '        model = (subProvider === "openai" && provider && typeof provider.chat === "function") ? provider.chat(subModelName) : provider(subModelName);';
const files = [
  "node_modules/@browserbasehq/stagehand/dist/cjs/lib/v3/llm/LLMProvider.js",
  "node_modules/@browserbasehq/stagehand/dist/esm/lib/v3/llm/LLMProvider.js",
];
let total = 0;
for (const f of files) {
  if (!fs.existsSync(f)) continue;
  let s = fs.readFileSync(f, "utf8");
  if (s.includes(NEW)) { console.log("already patched:", f); continue; }
  const n = s.split(ORIG).length - 1;
  s = s.split(ORIG).join(NEW);
  fs.writeFileSync(f, s);
  total += n; console.log(`patched ${n}x:`, f);
}
console.log("total replacements:", total);
