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
  link_text: "評価表", heading: "事業評価結果", page: 2, page_end: 3, segment: "sheet",
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

const projectFixture = {
  id: "hyoka-project:14130-kawasaki-shi:aaaaaaaaaaaaaaaa:aaaaaaaaaaaaaaaa-9", project_name: "市民ミュージアム管理運営事業",
  municipality_name: "川崎市", pref_name: "神奈川県", department: "市民文化局", evaluation_year: 2024,
  cost_yen: 1008569000, cost_year: 2024, cost_stage: "settlement", cost_scope: "total", cost_raw: "1,008,569", cost_unit: "千円",
  cost_prev_yen: 951217000, cost_change_ratio: 1.0603, staff_fte: 14.56, rating: "3", direction: "improve",
  direction_text: "Ⅱ（改善・見直し）", aspects: ["成果:b"], outcome_ratio_min: null, score: null,
  indicators: [{ name: "展覧会等の開催回数", kind: "activity", unit: "回", year: 2024, target: "4", actual: "10", ratio: 2.5,
    cost_per_unit_yen: 100856900, best_ratio: 2.5, first_year: 2022, first_actual: "20", change_ratio: 0.5 }],
  personnel_yen: 160000000, personnel_ratio: 0.1586, cost_per_person_yen: 56000, outcome_best_ratio_min: 0.537, actual_change_min: 0.5,
  flags: ["no_outcome_target", "renamed"], project_names: ["市民ミュージアム運営事業", "市民ミュージアム管理運営事業"],
  project_first_year: 2011, project_last_year: 2025, slug: "14130-kawasaki-shi",
  policy_names: ["5 市民自治の地域づくり", "5-2-1 人権尊重のまちづくり"], is_main: true, achievement: "below", overall: "middle",
  variant_base: "市民ミュージアム事業", variant_label: "収蔵品",
  location: "原典 21頁", source_page_link: "https://example.invalid/4-8.pdf#page=21",
  detail_url: "/search/detail/?id=hyoka%3A14130-kawasaki-shi%3Aaaaaaaaaaaaaaaaa&doc_type=hyoka",
  api_project_url: "/api/hyoka-project.php?id=x"
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
    if (url.pathname === "/api/hyoka-projects.php") {
      res.end(JSON.stringify({ status: "ok", query: url.searchParams.get("q") || "", sort: "cost_desc", page: 1, per_page: 10,
        total: 1, total_relation: "eq", items: [projectFixture] }));
      return;
    }
    if (url.pathname === "/api/hyoka-aggregate.php") {
      if (!url.searchParams.get("year")) {
        res.statusCode = 422;
        res.end(JSON.stringify({ status: "query_error", error: "year（金額の年度）を指定してください。" }));
        return;
      }
      res.end(JSON.stringify({ status: "ok", query: "", year: 2024, from_year: null, to_year: null, scope: "total", stage: "best",
        group_by: "municipality", matched_records: 3, total: { yen: 1234500000, projects: 2, municipalities: 1 },
        groups: [{ key: "14130-kawasaki-shi", label: "川崎市", yen: 1234500000, projects: 2 }], other_groups_yen: 0,
        top_projects: [{ id: projectFixture.id, project_name: projectFixture.project_name, municipality_name: "川崎市",
          department: "", year: 2024, stage: "settlement", label: "総コスト", raw: "1,008,569", unit: "千円", yen: 1008569000 }],
        notes: ["評価に載った事業だけの合計"] }));
      return;
    }
    if (url.pathname === "/api/hyoka-project.php") {
      res.end(JSON.stringify({ status: "ok", error: "", project: { ...projectFixture, amounts: [] },
        record: { fields: [{ key: "purpose", name: "目的", text: "博物館を運営する" }],
          policy_path: [{ level: "基本政策", code: "5", name: "市民自治の地域づくり" },
            { level: "施策", code: "5-2-1", name: "人権尊重のまちづくり", goal: "差別の無い社会" }],
          level: "事務事業", is_main: true,
          texts: [{ key: "description", name: "事業の内容・手段", label: "事業内容", text: "収蔵品を修復する" }],
          extra_fields: [{ section: "事業の概要", label: "事業開始年度", text: "1988" }],
          evaluations: [{ stage: "所管局", rating: "3", direction: "Ⅱ（改善・見直し）", aspects: [],
            achievement: { level: "below", name: "目標を下回る", raw: "1", option: "目標を下回った" },
            reasons: [{ aspect: "成果", label: "理由", text: "来館者が減った" }] }],
          amounts: { unit: "千円", columns: [{ year: 2024, stage: "決算" }], rows: [{ label: "総コスト", values: ["1,008,569"], yen: [1008569000] }] } },
        sheet: [{ page: 21, text: "事務事業名 | 市民ミュージアム管理運営事業" }], sheet_truncated: false }));
      return;
    }
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
      ["aggregate_hyoka_amounts", "get_hyoka_project", "get_municipal_document", "search_hyoka", "search_hyoka_projects",
        "search_minutes", "search_reiki"]);
    for (const name of ["aggregate_hyoka_amounts", "get_hyoka_project", "search_hyoka_projects"]) {
      assert.equal(result.tools.find(tool => tool.name === name).annotations.readOnlyHint, true);
    }
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
    assert.match(result.content[0].text, /事業のシート \/ 2〜3頁/);
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

  await t.test("searches projects with filters and formats costs in yen", async () => {
    const result = await rpc(base, "tools/call", { name: "search_hyoka_projects", arguments: {
      slug: "14130-kawasaki-shi", direction: ["improve", "expand"], rating: ["4"], below_target: true,
      min_cost: 100000000, min_cost_change_ratio: 1.5, sort: "cost_change_desc", per_page: 5
    } });
    assert.notEqual(result.isError, true, JSON.stringify(result));
    const query = requests.at(-1).searchParams;
    assert.equal(requests.at(-1).pathname, "/api/hyoka-projects.php");
    for (const [key, value] of Object.entries({ slug: "14130-kawasaki-shi", direction: "improve,expand", rating: "4",
      below_target: "1", min_cost: "100000000", min_cost_change_ratio: "1.5", sort: "cost_change_desc", per_page: "5" })) {
      assert.equal(query.get(key), value);
    }
    const item = result.structuredContent.items[0];
    assert.equal(item.outcome_ratio_min, undefined);
    assert.match(item.detail_url, /^https:\/\/public.invalid\/search\/detail\//);
    assert.match(item.api_project_url, /^https:\/\/public.invalid\/api\/hyoka-project\.php/);
    const text = result.content[0].text;
    assert.match(text, /10億857万円/);
    assert.match(text, /原典 1,008,569千円/);
    assert.match(text, /前年度比 106%（前年度 9億5,122万円）/);
    assert.match(text, /方向性 改善・見直し/);
    assert.match(text, /展覧会等の開催回数 2024年度 目標 4 実績 10回（250%） \/ 1回あたり 1億86万円 \/ 最良の年度 250% \/ 2022年度の 20 から 50%/);
    assert.match(text, /計算: 人件費 1億6,000万円（費用の 15.9%） \/ 参加者・利用者など 1 人あたり 6万円 \/ 成果指標の最良の年度の比（指標のうち最も低いもの） 53.7% \/ 指標の実績の増減（最初の年度から） 最小 50%/);
    assert.match(text, /印: 目標の数のある成果指標が無い \/ 事業名が年度で違う/);
    assert.match(text, /履歴: 2011〜2025年度の評価に載る \/ 名前: 市民ミュージアム運営事業 → 市民ミュージアム管理運営事業/);
    const flagged = await rpc(base, "tools/call", { name: "search_hyoka_projects", arguments: {
      flag: ["outcome_below_half", "cost_up_below_target"] } });
    assert.notEqual(flagged.isError, true, JSON.stringify(flagged));
    assert.equal(requests.at(-1).searchParams.get("flag"), "outcome_below_half,cost_up_below_target");
    const trends = await rpc(base, "tools/call", { name: "search_hyoka_projects", arguments: {
      max_outcome_best_ratio: 0.6, max_actual_change_ratio: 0.5, min_personnel_ratio: 0.7, min_cost_per_person: 50000,
      flag: ["below_target_every_year", "actual_halved", "blank_sheet"], sort: "personnel_ratio_desc" } });
    assert.notEqual(trends.isError, true, JSON.stringify(trends));
    for (const [key, value] of Object.entries({ max_outcome_best_ratio: "0.6", max_actual_change_ratio: "0.5", min_personnel_ratio: "0.7",
      min_cost_per_person: "50000", flag: "below_target_every_year,actual_halved,blank_sheet", sort: "personnel_ratio_desc" })) {
      assert.equal(requests.at(-1).searchParams.get(key), value);
    }
    const tooHigh = await rpc(base, "tools/call", { name: "search_hyoka_projects", arguments: { min_personnel_ratio: 1.5 } });
    assert.equal(tooHigh.isError, true);
    const rejected = await rpc(base, "tools/call", { name: "search_hyoka_projects", arguments: { flag: ["wasteful"] } });
    assert.equal(rejected.isError, true);
    // 形 2.0 の体系・そろえた評価・主な事業での絞り込み
    const form2 = await rpc(base, "tools/call", { name: "search_hyoka_projects", arguments: {
      policy: "5-2-1", achievement: ["below"], overall: ["low", "middle"], is_main: true } });
    assert.notEqual(form2.isError, true, JSON.stringify(form2));
    for (const [key, value] of Object.entries({ policy: "5-2-1", achievement: "below", overall: "low,middle", is_main: "1" })) {
      assert.equal(requests.at(-1).searchParams.get(key), value);
    }
    assert.match(form2.content[0].text, /体系: 5 市民自治の地域づくり > 5-2-1 人権尊重のまちづくり（主な事業）/);
    assert.match(form2.content[0].text, /評価 3 \/ 達成度 目標を下回る \/ 総合 中くらい/);
    assert.match(form2.content[0].text, /分け: 市民ミュージアム事業 の 収蔵品/);
    const badLevel = await rpc(base, "tools/call", { name: "search_hyoka_projects", arguments: { achievement: ["bad"] } });
    assert.equal(badLevel.isError, true);
  });

  await t.test("aggregates amounts and reports REST validation errors", async () => {
    const result = await rpc(base, "tools/call", { name: "aggregate_hyoka_amounts", arguments: {
      year: 2024, group_by: "municipality", pref_code: "14", top: 3
    } });
    assert.notEqual(result.isError, true, JSON.stringify(result));
    const query = requests.at(-1).searchParams;
    assert.equal(requests.at(-1).pathname, "/api/hyoka-aggregate.php");
    for (const [key, value] of Object.entries({ year: "2024", group_by: "municipality", scope: "total", stage: "best", top: "3" })) {
      assert.equal(query.get(key), value);
    }
    assert.match(result.content[0].text, /合計 12億3,450万円/);
    assert.match(result.content[0].text, /川崎市（14130-kawasaki-shi）: 12億3,450万円 \/ 2事業/);
    assert.match(result.content[0].text, /注: 評価に載った事業だけの合計/);
    const byPolicy = await rpc(base, "tools/call", { name: "aggregate_hyoka_amounts", arguments: {
      year: 2024, group_by: "policy_top", stage: "initial_budget" } });
    assert.notEqual(byPolicy.isError, true, JSON.stringify(byPolicy));
    assert.equal(requests.at(-1).searchParams.get("group_by"), "policy_top");
    assert.equal(requests.at(-1).searchParams.get("stage"), "initial_budget");
    const failed = await rpc(base, "tools/call", { name: "aggregate_hyoka_amounts", arguments: { group_by: "department" } });
    assert.equal(failed.isError, true);
    assert.match(failed.content[0].text, /year/);
  });

  await t.test("returns one project with its sheet text", async () => {
    const result = await rpc(base, "tools/call", { name: "get_hyoka_project", arguments: { id: projectFixture.id, max_sheet_chars: 500 } });
    assert.notEqual(result.isError, true, JSON.stringify(result));
    assert.equal(requests.at(-1).searchParams.get("id"), projectFixture.id);
    assert.equal(requests.at(-1).searchParams.get("max_sheet_chars"), "500");
    const text = result.content[0].text;
    assert.match(text, /目的: 博物館を運営する/);
    assert.match(text, /金額（円にそろえ万円未満は四捨五入。原典の単位は千円）/);
    assert.match(text, /総コスト \| 10億857万円/);
    assert.match(text, /原典 21頁の表/);
    assert.match(text, /体系の道筋:\n   基本政策 5 市民自治の地域づくり\n   施策 5-2-1 人権尊重のまちづくり \/ 目標: 差別の無い社会/);
    assert.match(text, /階層: 事務事業 \/ 主な事業（体系図の○）/);
    assert.match(text, /事業の内容・手段（原典: 事業内容）: 収蔵品を修復する/);
    assert.match(text, /そのほかの欄:\n   事業の概要 \/ 事業開始年度: 1988/);
    assert.match(text, /達成度（そろえた段階）: 目標を下回る（原典 1「目標を下回った」）/);
    assert.match(text, /理由（成果）: 来館者が減った/);
    assert.match(result.structuredContent.project.detail_url, /^https:\/\/public.invalid\//);
    assert.equal(result.structuredContent.related, undefined);
    const before = requests.length;
    const related = await rpc(base, "tools/call", { name: "get_hyoka_project", arguments: { id: projectFixture.id, include_related: true } });
    assert.notEqual(related.isError, true, JSON.stringify(related));
    const searches = requests.slice(before).filter(url => url.pathname === "/api/search");
    assert.deepEqual(searches.map(url => url.searchParams.get("doc_type")).sort(), ["minutes", "reiki"]);
    for (const url of searches) {
      assert.equal(url.searchParams.get("q"), `"${projectFixture.project_name}"`);
      assert.equal(url.searchParams.get("slug"), "14130-kawasaki-shi");
      assert.equal(url.searchParams.get("per_page"), "3");
    }
    assert.equal(related.structuredContent.related.minutes.total, 1);
    assert.match(related.content[0].text, /事業名が出てくる会議録: 1件/);
    assert.match(related.content[0].text, /事業名が出てくる例規集: 1件/);
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
