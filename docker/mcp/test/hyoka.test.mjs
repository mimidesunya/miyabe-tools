// 公開APIをローカルの固定応答に置き換える。OpenSearch・外部サービスには繋がない。
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { mkdtemp, rm } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, join, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath } from "node:url";
import test from "node:test";

const fixture = {
  id: "hyoka:14216-zama-shi:sample:p2", doc_type: "hyoka",
  title: "子育て支援事業評価", municipality_name: "座間市", pref_name: "神奈川県",
  fiscal_year: 2024, year_label: "令和6年度", kind: "project_evaluation",
  link_text: "評価表", heading: "事業評価結果", page: 2,
  source_url: "https://example.invalid/evaluation.pdf",
  source_page_link: "https://example.invalid/evaluation.pdf#page=2",
  source_page_url: "https://example.invalid/evaluation/",
  detail_url: "/search/detail/?doc_type=hyoka&id=sample",
  api_document_url: "/api/document?doc_type=hyoka&id=sample",
  sha256: "a".repeat(64), fetched_at: "2026-10-29T10:00:00+09:00",
  status: "gone", text_status: "ok", page_split_reason: "form_feed",
  excerpt: "子育て支援を実施", body: "子育て支援を実施しました。", body_length: 14,
  score: null, proximity: null
};

async function listen(server) {
  server.listen(0, "127.0.0.1");
  await once(server, "listening");
  return server.address().port;
}

async function close(server) {
  server.close();
  server.closeAllConnections();
  await once(server, "close");
}

async function rpc(base, method, params = {}) {
  const response = await fetch(`${base}/mcp`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "application/json, text/event-stream" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params })
  });
  assert.equal(response.status, 200);
  const text = await response.text();
  const payload = response.headers.get("content-type").includes("text/event-stream")
    ? JSON.parse(text.split("\n").find(line => line.startsWith("data: ")).slice(6))
    : JSON.parse(text);
  assert.equal(payload.error, undefined, JSON.stringify(payload.error));
  return payload.result;
}

test("hyoka MCP contract through a local public API mock", { timeout: 30000 }, async (t) => {
  const mcpRoot = fileURLToPath(new URL("../", import.meta.url));
  const buildDir = await mkdtemp(join(mcpRoot, "test", "build-"));
  assert.equal(dirname(resolve(buildDir)), resolve(mcpRoot, "test"));
  t.after(() => rm(buildDir, { recursive: true, force: true }));
  const compiler = spawn(process.execPath, [join(mcpRoot, "node_modules", "typescript", "bin", "tsc"),
    "-p", join(mcpRoot, "tsconfig.json"), "--outDir", buildDir], { stdio: "pipe", windowsHide: true });
  let compilerOutput = "";
  compiler.stdout.on("data", chunk => { compilerOutput += chunk; });
  compiler.stderr.on("data", chunk => { compilerOutput += chunk; });
  assert.equal((await once(compiler, "exit"))[0], 0, compilerOutput);
  const requests = [];
  const api = createServer((req, res) => {
    const url = new URL(req.url, "http://127.0.0.1");
    requests.push(url);
    const docType = url.searchParams.get("doc_type");
    const document = { ...fixture, doc_type: docType };
    if (url.searchParams.get("id") === "whole") {
      document.page = null;
      document.fiscal_year = null;
      document.source_page_link = document.source_url;
      document.page_split_reason = "saved_text_no_page_boundaries";
    }
    res.setHeader("Content-Type", "application/json");
    res.end(JSON.stringify(url.pathname === "/api/document"
      ? { status: "ok", document }
      : { status: "ok", query: url.searchParams.get("q"), doc_type: docType,
          page: 1, per_page: 10, total: 1, items: [document] }));
  });
  const apiPort = await listen(api);
  const portProbe = createServer();
  const mcpPort = await listen(portProbe);
  await close(portProbe);
  const base = `http://127.0.0.1:${mcpPort}`;
  const child = spawn(process.execPath, [join(buildDir, "server.js")], {
    env: { ...process.env, PORT: String(mcpPort), MIYABE_API_BASE_URL: `http://127.0.0.1:${apiPort}`,
      MIYABE_PUBLIC_BASE_URL: "https://public.invalid", MCP_ALLOWED_HOSTS: "", MCP_ALLOWED_ORIGINS: "" },
    stdio: ["ignore", "pipe", "pipe"], windowsHide: true
  });
  let output = "";
  child.stdout.on("data", chunk => { output += chunk; });
  child.stderr.on("data", chunk => { output += chunk; });
  t.after(async () => {
    if (child.exitCode === null) {
      const exited = once(child, "exit");
      child.kill();
      await exited;
    }
    await close(api);
  });
  let ready = false;
  for (let attempt = 0; attempt < 100; attempt++) {
    if (child.exitCode !== null) throw new Error(output);
    try {
      ready = (await fetch(`${base}/healthz`)).ok;
      if (ready) break;
    } catch { /* startup */ }
    await delay(50);
  }
  assert.ok(ready, output);

  await t.test("lists hyoka filters and permits hyoka document reads", async () => {
    const result = await rpc(base, "tools/list");
    assert.deepEqual(result.tools.map(tool => tool.name).sort(),
      ["get_municipal_document", "search_hyoka", "search_minutes", "search_reiki"]);
    const hyoka = result.tools.find(tool => tool.name === "search_hyoka");
    assert.deepEqual(hyoka.inputSchema.properties.kind.enum,
      ["project_evaluation", "policy_evaluation", "external_review", "overview"]);
    assert.equal(hyoka.inputSchema.properties.fiscal_year.minimum, 1000);
    assert.equal(hyoka.inputSchema.properties.start_date, undefined);
    assert.equal(hyoka.annotations.readOnlyHint, true);
    assert.ok(result.tools.find(tool => tool.name === "get_municipal_document")
      .inputSchema.properties.doc_type.enum.includes("hyoka"));
  });

  await t.test("passes filters to REST and retains page provenance and gone status", async () => {
    const result = await rpc(base, "tools/call", { name: "search_hyoka", arguments: {
      q: "子育て", fiscal_year: 2024, kind: "project_evaluation", pref_code: "14",
      slug: "14216-zama-shi", municipality_code: "142166", page: 2, per_page: 5
    } });
    assert.notEqual(result.isError, true, JSON.stringify(result));
    const query = requests.at(-1).searchParams;
    for (const [key, value] of Object.entries({ doc_type: "hyoka", fiscal_year: "2024",
      kind: "project_evaluation", pref_code: "14", slug: "14216-zama-shi",
      municipality_code: "142166", page: "2", per_page: "5", sort: "relevance" })) {
      assert.equal(query.get(key), value);
    }
    const item = result.structuredContent.items[0];
    for (const key of ["source_url", "source_page_url", "source_page_link", "page", "status", "sha256", "kind", "fiscal_year"]) {
      assert.equal(item[key], fixture[key]);
    }
    assert.match(item.api_document_url, /^https:\/\/public.invalid\/api\/document/);
    assert.match(item.detail_url, /^https:\/\/public.invalid\/search\/detail\//);
    assert.match(result.content[0].text, /事務事業評価検索/);
    assert.match(result.content[0].text, /2頁/);
    assert.match(result.content[0].text, /取得元から消失/);
    assert.equal(item.score, undefined);
  });

  await t.test("returns page body or unpaginated body without fabricated page", async () => {
    for (const id of [fixture.id, "whole"]) {
      const result = await rpc(base, "tools/call", { name: "get_municipal_document", arguments: {
        id, doc_type: "hyoka", max_body_chars: 4
      } });
      assert.notEqual(result.isError, true, JSON.stringify(result));
      assert.equal(requests.at(-1).searchParams.get("doc_type"), "hyoka");
      const document = result.structuredContent.document;
      assert.equal(document.body, fixture.body.slice(0, 4));
      assert.equal(document.body_full_length, fixture.body.length);
      assert.equal(document.body_truncated, true);
      assert.equal(document.page, id === "whole" ? undefined : 2);
      if (id === "whole") {
        assert.equal(document.fiscal_year, undefined);
        assert.match(result.content[0].text, /頁区切りなし/);
      }
    }
  });

  await t.test("rejects invalid hyoka filters before calling REST", async () => {
    for (const args of [{ fiscal_year: 99 }, { kind: "minutes" }]) {
      const before = requests.length;
      const result = await rpc(base, "tools/call", { name: "search_hyoka", arguments: { q: "子育て", ...args } });
      assert.equal(result.isError, true);
      assert.equal(requests.length, before);
    }
  });

  await t.test("keeps existing minutes/reiki default sort and filters", async () => {
    for (const [docType, sort] of [["minutes", "date"], ["reiki", "relevance"]]) {
      const result = await rpc(base, "tools/call", { name: `search_${docType}`, arguments: {
        q: "子育て", start_year: 2020, end_year: 2024
      } });
      assert.notEqual(result.isError, true, JSON.stringify(result));
      const query = requests.at(-1).searchParams;
      assert.equal(query.get("doc_type"), docType);
      assert.equal(query.get("sort"), sort);
      assert.equal(query.get("start_year"), "2020");
      assert.equal(query.get("fiscal_year"), null);
      assert.equal(query.get("kind"), null);
    }
  });
});
