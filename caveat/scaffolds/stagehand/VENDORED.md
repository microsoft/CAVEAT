# Stagehand harness (harness #2)

[Stagehand](https://github.com/browserbase/stagehand) (Browserbase) — a popular
modern Playwright-based web agent. Replaces the older WebVoyager harness, which
couldn't drive this React SPA's checkout (looped on dropdowns, ~half its runs
ended in "none"). Stagehand actually completes the home→search→detail→cart→
checkout→purchase flow.

- `run_stagehand.mjs` — Node runner: Stagehand v3 `agent()` in **`dom` mode**
  (DOM/aria-tree act+fillForm — its recommended mode for regular chat models;
  works for every model incl. text-only). Drives a local Chromium
  (`chromium-1223`, `LD_LIBRARY_PATH` from `.chromium-deps`, see
  [[playwright-wsl-newdistro]]). Stagehand runs autonomously in one
  `execute()`, so we poll `page.screenshot()` concurrently and pair frames to
  the agent's actions for the replay viewer.
- `run_stagehand.py` — Python wrapper (mirrors `run_webvoyager.py`): seeds the
  caveat_shop server, sets env, runs the Node agent, normalizes output → shared
  `step_NN.png` + `trace.json` + `summary.json`.

## TRAPI compatibility fixes (required for non-OpenAI models)

Stagehand uses the Vercel AI SDK (`@ai-sdk/openai`). Three issues surfaced
pointing it at TRAPI's `gcr/shared` deployments:

1. **Responses API → encrypted reasoning.** `@ai-sdk/openai` v3 defaults
   `openai(id)` to the Responses API, which sends encrypted reasoning content
   that non-OpenAI deployments reject ("Encrypted content is not supported").
   **Fix:** `patch_stagehand.mjs` patches `dist/{cjs,esm}/lib/v3/llm/LLMProvider.js`
   to use `provider.chat(id)` (chat-completions) for the openai provider.
   *Idempotent — re-run after `npm install`.*
2. **`role: "developer"`.** The AI SDK emits the system prompt as a `developer`
   role; TRAPI's Kimi/Llama only accept `system/user/assistant/tool`.
   **Fix:** a `fetch` shim in `run_stagehand.mjs` rewrites `developer`→`system`
   on outgoing `chat/completions` bodies (harmless for OpenAI models).
3. **Parallel tool calls.** Llama-3.3-70B rejects multiple tool calls. The shim
   also injects `parallel_tool_calls: false` when `tools` are present.

## Model coverage

Works: **gpt-5.5, gpt-4.1, gpt-4o, Kimi-K2.6** (4/5). **Llama-3.3-70B** is
incompatible — TRAPI reports it "does not support more than one tool call,"
and Stagehand's agent needs a multi-tool toolset; it's a real model capability
limit, not a harness bug. (Llama still has full browser-use coverage.)

Setup: `npm install` here, then `node patch_stagehand.mjs`.
