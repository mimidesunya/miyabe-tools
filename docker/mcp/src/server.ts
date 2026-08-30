import express, { type Request, type Response, type NextFunction } from "express";
import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StreamableHTTPServerTransport } from "@modelcontextprotocol/sdk/server/streamableHttp.js";
import * as z from "zod/v4";

const apiBaseUrl = (process.env.MIYABE_API_BASE_URL || "http://web").replace(/\/+$/, "");
const port = Number.parseInt(process.env.PORT || "3000", 10);
const requestTimeoutMs = Number.parseInt(process.env.MCP_API_TIMEOUT_MS || "20000", 10);
const allowedHosts = parseCsv(process.env.MCP_ALLOWED_HOSTS || "");
const allowedOrigins = parseCsv(process.env.MCP_ALLOWED_ORIGINS || "");

type DocType = "minutes" | "reiki";

class PublicApiError extends Error {
  constructor(
    message: string,
    public readonly httpStatus: number,
    public readonly payload: unknown
  ) {
    super(message);
  }
}

function parseCsv(value: string): string[] {
  return value
    .split(",")
    .map((item) => item.trim().toLowerCase())
    .filter((item) => item !== "");
}

function requestHost(req: Request): string {
  const host = String(req.headers.host || "");
  return host.split(":")[0].trim().toLowerCase();
}

function hostGuard(req: Request, res: Response, next: NextFunction): void {
  if (allowedHosts.length === 0 || req.path === "/healthz") {
    next();
    return;
  }

  if (allowedHosts.includes(requestHost(req))) {
    next();
    return;
  }

  res.status(421).json({ error: "Host is not allowed for this MCP endpoint." });
}

function originGuard(req: Request, res: Response, next: NextFunction): void {
  if (allowedOrigins.length === 0 || req.path === "/healthz") {
    next();
    return;
  }

  const origin = String(req.headers.origin || "").trim();
  if (origin === "") {
    next();
    return;
  }

  try {
    const originHost = new URL(origin).host.toLowerCase();
    if (allowedOrigins.includes(origin.toLowerCase()) || allowedOrigins.includes(originHost)) {
      res.setHeader("Access-Control-Allow-Origin", origin);
      res.setHeader("Vary", "Origin");
      next();
      return;
    }
  } catch {
    // Fall through to the 403 below.
  }

  res.status(403).json({ error: "Origin is not allowed for this MCP endpoint." });
}

function addOptional(params: URLSearchParams, key: string, value: unknown): void {
  if (value === undefined || value === null) {
    return;
  }
  if (typeof value === "string" && value.trim() === "") {
    return;
  }
  if (typeof value === "boolean") {
    params.set(key, value ? "1" : "0");
    return;
  }
  params.set(key, String(value));
}

async function callPublicApi(path: string, params: URLSearchParams): Promise<any> {
  const url = new URL(path, apiBaseUrl + "/");
  params.forEach((value, key) => url.searchParams.set(key, value));

  const response = await fetch(url, {
    headers: {
      Accept: "application/json",
      "User-Agent": "miyabe-tools-mcp/0.1"
    },
    signal: AbortSignal.timeout(requestTimeoutMs)
  });
  const body = await response.text();

  let payload: any = {};
  try {
    payload = body !== "" ? JSON.parse(body) : {};
  } catch {
    throw new PublicApiError(`Public API returned non-JSON response (${response.status}).`, response.status, body);
  }

  if (!response.ok) {
    const message = typeof payload?.error === "string" && payload.error !== ""
      ? payload.error
      : `Public API returned HTTP ${response.status}.`;
    throw new PublicApiError(message, response.status, payload);
  }

  return payload;
}

function buildSearchParams(docType: DocType, args: any): URLSearchParams {
  const params = new URLSearchParams();
  params.set("doc_type", docType);
  params.set("q", String(args.q || ""));
  addOptional(params, "slug", args.slug);
  addOptional(params, "municipality_code", args.municipality_code);
  addOptional(params, "pref_code", args.pref_code);
  addOptional(params, "start_date", args.start_date);
  addOptional(params, "end_date", args.end_date);
  addOptional(params, "start_year", args.start_year);
  addOptional(params, "end_year", args.end_year);
  // 会議録は新しい発言から見たいので日付順が既定。例規は制定が古い基本規則ほど
  // 重要なことが多く、日付順だと本則が下へ沈む（「補助金等交付規則」で本則が15位、
  // 関連度順なら1位）。例規は関連度順を既定にする。
  addOptional(params, "sort", args.sort || (docType === "reiki" ? "relevance" : "date"));
  addOptional(params, "page", args.page || 1);
  addOptional(params, "per_page", args.per_page || 10);
  addOptional(params, "include_facets", args.include_facets);
  addOptional(params, "include_body_highlight", args.include_body_highlight);
  return params;
}

function displayDate(item: any): string {
  for (const key of ["held_on", "promulgated_on", "sort_date", "updated_at"]) {
    const value = String(item?.[key] || "").trim();
    if (value !== "") {
      return value;
    }
  }
  return "";
}

function formatSearchText(payload: any): string {
  const items = Array.isArray(payload?.items) ? payload.items : [];
  const header = `${payload?.doc_type === "reiki" ? "例規集" : "会議録"}検索: "${payload?.query || ""}" / ${payload?.total ?? 0}件`;
  if (items.length === 0) {
    return `${header}\n該当する結果はありませんでした。`;
  }

  const lines = items.map((item: any, index: number) => {
    const meta = [
      item.pref_name,
      item.municipality_name,
      item.meeting_name,
      item.category,
      displayDate(item)
    ].filter((value) => String(value || "").trim() !== "").join(" / ");
    const excerpt = String(item.excerpt || "").replace(/\s+/g, " ").trim();
    const urls = [
      item.detail_url ? `detail: ${item.detail_url}` : "",
      item.source_url ? `source: ${item.source_url}` : ""
    ].filter(Boolean).join(" / ");
    return [
      `${index + 1}. ${item.title || "(無題)"}`,
      meta !== "" ? `   ${meta}` : "",
      item.id ? `   id: ${item.id}` : "",
      excerpt !== "" ? `   excerpt: ${excerpt}` : "",
      urls !== "" ? `   ${urls}` : ""
    ].filter(Boolean).join("\n");
  });

  return [header, ...lines].join("\n");
}

function truncateBody(document: any, maxChars: number): any {
  const body = String(document?.body || "");
  const limited = Math.max(0, Math.min(maxChars, 200000));
  if (body.length <= limited) {
    return { ...document, body_full_length: body.length, body_truncated: false };
  }
  return {
    ...document,
    body: body.slice(0, limited),
    body_full_length: body.length,
    body_truncated: true
  };
}

function formatDocumentText(document: any): string {
  const meta = [
    document.pref_name,
    document.municipality_name,
    document.assembly_name,
    document.meeting_name,
    document.category,
    displayDate(document)
  ].filter((value) => String(value || "").trim() !== "").join(" / ");
  const body = String(document.body || "");
  return [
    `${document.title || "(無題)"}`,
    meta,
    document.id ? `id: ${document.id}` : "",
    document.source_url ? `source: ${document.source_url}` : "",
    document.detail_url ? `detail: ${document.detail_url}` : "",
    document.body_truncated ? `body: ${body}\n\n[本文は ${body.length}/${document.body_full_length} 文字で省略されています。max_body_chars を増やすと取得量を増やせます。]` : `body: ${body}`
  ].filter((value) => String(value || "").trim() !== "").join("\n");
}

function errorResult(error: unknown) {
  if (error instanceof PublicApiError) {
    return {
      isError: true,
      content: [{ type: "text" as const, text: error.message }],
      structuredContent: {
        status: "error",
        http_status: error.httpStatus,
        error: error.message,
        detail: error.payload
      }
    };
  }

  const message = error instanceof Error ? error.message : "Unknown MCP server error.";
  return {
    isError: true,
    content: [{ type: "text" as const, text: message }],
    structuredContent: {
      status: "error",
      error: message
    }
  };
}

const searchInputSchema = {
  q: z.string().min(1).describe("検索語。空白区切りはAND検索、引用符で完全一致、OR/NOT/括弧も利用できます。"),
  pref_code: z.string().optional().describe("都道府県コード。例: 神奈川県は 14。"),
  slug: z.string().optional().describe("自治体を1つに絞るcanonical slug。例: 14130-kawasaki-shi。"),
  municipality_code: z.string().optional().describe("全国地方公共団体コード。"),
  start_date: z.string().optional().describe("検索対象期間の開始日。YYYY-MM-DD形式。"),
  end_date: z.string().optional().describe("検索対象期間の終了日。YYYY-MM-DD形式。"),
  start_year: z.number().int().min(1).max(9999).optional().describe("検索対象期間の開始年。"),
  end_year: z.number().int().min(1).max(9999).optional().describe("検索対象期間の終了年。"),
  sort: z.enum(["date", "relevance"]).optional().describe("date は新しい順、relevance は関連度順。既定は会議録が date、例規集が relevance。"),
  page: z.number().int().min(1).default(1).describe("ページ番号。"),
  per_page: z.number().int().min(1).max(50).default(10).describe("1ページあたりの件数。MCPでは最大50件。"),
  include_facets: z.boolean().optional().describe("true の場合、文書種別・都道府県・自治体の集計も返します。"),
  include_body_highlight: z.boolean().optional().describe("false の場合、本文ハイライト生成を抑制します。")
};

// 会議録と例規集で意味を持つフィールドが違い、API側の項目も増えうる。
// 常に入るものだけ必須にし、残りは任意にして検証で落ちないようにする。
const documentCommonShape = {
  id: z.string().describe("文書ID。get_municipal_document にそのまま渡せる。"),
  doc_type: z.string().describe("minutes は会議録、reiki は例規集。"),
  slug: z.string().optional().describe("自治体のcanonical slug。例: 14130-kawasaki-shi。"),
  municipality_code: z.string().optional().describe("全国地方公共団体コード。"),
  pref_code: z.string().optional().describe("都道府県コード。"),
  pref_name: z.string().optional().describe("都道府県名。"),
  municipality_name: z.string().optional().describe("自治体名。"),
  title: z.string().optional().describe("文書のタイトル。"),
  title_highlight: z.string().optional().describe("タイトル中の一致箇所を示した文字列。"),
  excerpt: z.string().optional().describe("本文中の該当箇所の抜粋。[[[ ]]] で囲まれた部分が一致箇所。"),
  score: z.number().optional().describe("検索スコア。関連度順のときの並び順に対応する。"),
  body_length: z.number().optional().describe("本文全体の文字数。"),
  source_url: z.string().optional().describe("取得元（自治体側）のURL。"),
  detail_url: z.string().optional().describe("本サービスの詳細ページのパス。"),
  api_document_url: z.string().optional().describe("本文取得APIのパス。"),
  source_file: z.string().optional().describe("取得元のファイルパス。"),
  source_system: z.string().optional().describe("取得元システムの種別。例: dbsr、kaigiroku.net。"),
  updated_at: z.string().optional().describe("索引の更新日時。"),
  sort_date: z.string().optional().describe("並び替えに使う日付。"),
  local_id: z.string().optional().describe("自治体内での文書ID。"),
  filename: z.string().optional().describe("取得元のファイル名。"),
  assembly_name: z.string().optional().describe("議会名。会議録で入る。"),
  meeting_name: z.string().optional().describe("会議名。会議録で入る。"),
  year_label: z.string().optional().describe("年度の表記。"),
  held_on: z.string().optional().describe("開催日。会議録で入る。"),
  ordinance_no: z.string().optional().describe("例規番号。例規集で入る。"),
  category: z.string().optional().describe("分類。例規集で入る。"),
  promulgated_on: z.string().optional().describe("公布日。例規集で入る。"),
  enforced_on: z.string().optional().describe("施行日。例規集で入る。"),
  amended_on: z.string().optional().describe("最終改正日。例規集で入る。")
};

const facetBucketSchema = z.object({
  key: z.string().describe("集計キー。"),
  count: z.number().describe("該当件数。")
});

const searchOutputSchema = {
  status: z.string().describe("ok または error。"),
  error: z.string().optional().describe("エラーメッセージ。正常時は空文字。"),
  doc_type: z.string().describe("minutes は会議録、reiki は例規集。"),
  query: z.string().describe("実際に検索へ渡された検索語。"),
  page: z.number().describe("現在のページ番号。"),
  per_page: z.number().describe("1ページあたりの件数。"),
  total: z.number().describe("ヒット総数。total_relation が gte のときは下限値。"),
  total_relation: z.string().optional().describe("eq は総数が確定、gte は total 以上あることを示す。"),
  has_more: z.boolean().optional().describe("次のページがあるか。"),
  took_ms: z.number().optional().describe("検索にかかった時間（ミリ秒）。"),
  index_alias: z.string().optional().describe("検索に使った索引の別名。"),
  items: z.array(z.object(documentCommonShape)).describe("検索結果。id を get_municipal_document に渡すと本文を取得できる。"),
  aggregations: z.object({
    doc_types: z.array(facetBucketSchema).optional().describe("文書種別ごとの件数。"),
    prefectures: z.array(facetBucketSchema).optional().describe("都道府県ごとの件数。key は都道府県コード。"),
    municipalities: z.array(facetBucketSchema).optional().describe("自治体ごとの件数。key は canonical slug。")
  }).optional().describe("include_facets が true のときだけ返る集計結果。")
};

const documentOutputSchema = {
  status: z.string().describe("ok または error。"),
  error: z.string().optional().describe("エラーメッセージ。正常時は空文字。"),
  document: z.object({
    ...documentCommonShape,
    indexed_at: z.string().optional().describe("索引に取り込んだ日時。"),
    speaker: z.string().optional().describe("発言者名。"),
    speaker_role: z.string().optional().describe("発言者の役職。"),
    body: z.string().describe("本文。max_body_chars を超える分は切り詰められる。"),
    body_full_length: z.number().describe("切り詰める前の本文の文字数。"),
    body_truncated: z.boolean().describe("本文が切り詰められたかどうか。")
  }).describe("取得した文書。")
};

// 3ツールとも公開データを読むだけで、書き込みも削除も行わない。注釈がないと
// クライアントは既定で「書き込みあり・破壊的」とみなすため、明示しておく。
const READ_ONLY_ANNOTATIONS = {
  readOnlyHint: true,
  destructiveHint: false,
  idempotentHint: true,
  openWorldHint: true
} as const;

function createServer(): McpServer {
  const server = new McpServer({
    name: "miyabe-tools-search",
    version: "0.1.0"
  });

  server.registerTool(
    "search_minutes",
    {
      title: "会議録検索",
      description: "全国自治体の会議録を検索し、該当箇所の抜粋、自治体、開催日、原典URL、全文取得用IDを返します。",
      inputSchema: searchInputSchema,
      outputSchema: searchOutputSchema,
      annotations: READ_ONLY_ANNOTATIONS
    },
    async (args) => {
      try {
        const payload = await callPublicApi("/api/search", buildSearchParams("minutes", args));
        return {
          content: [{ type: "text" as const, text: formatSearchText(payload) }],
          structuredContent: payload
        };
      } catch (error) {
        return errorResult(error);
      }
    }
  );

  server.registerTool(
    "search_reiki",
    {
      title: "例規集検索",
      description: "全国自治体の条例・規則などの例規集を検索し、該当箇所の抜粋、自治体、公布日等、原典URL、全文取得用IDを返します。",
      inputSchema: searchInputSchema,
      outputSchema: searchOutputSchema,
      annotations: READ_ONLY_ANNOTATIONS
    },
    async (args) => {
      try {
        const payload = await callPublicApi("/api/search", buildSearchParams("reiki", args));
        return {
          content: [{ type: "text" as const, text: formatSearchText(payload) }],
          structuredContent: payload
        };
      } catch (error) {
        return errorResult(error);
      }
    }
  );

  server.registerTool(
    "get_municipal_document",
    {
      title: "文書本文取得",
      description: "search_minutes/search_reiki の結果IDから、会議録または例規集の本文を取得します。長文は max_body_chars で返却量を調整できます。",
      inputSchema: {
        id: z.string().min(1).describe("検索結果の id。"),
        doc_type: z.enum(["minutes", "reiki"]).default("minutes").describe("minutes は会議録、reiki は例規集。"),
        max_body_chars: z.number().int().min(0).max(200000).default(20000).describe("返却する本文の最大文字数。最大200000。")
      },
      outputSchema: documentOutputSchema,
      annotations: READ_ONLY_ANNOTATIONS
    },
    async (args) => {
      try {
        const params = new URLSearchParams();
        params.set("id", args.id);
        params.set("doc_type", args.doc_type || "minutes");
        const payload = await callPublicApi("/api/document", params);
        const document = truncateBody(payload.document || {}, args.max_body_chars ?? 20000);
        const result = { ...payload, document };
        return {
          content: [{ type: "text" as const, text: formatDocumentText(document) }],
          structuredContent: result
        };
      } catch (error) {
        return errorResult(error);
      }
    }
  );

  return server;
}

const app = express();
app.use(express.json({ limit: "1mb" }));
app.use(hostGuard);
app.use(originGuard);

app.options("/mcp", (req, res) => {
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type, Accept, MCP-Protocol-Version, Mcp-Session-Id");
  res.status(204).end();
});

app.get("/healthz", (req, res) => {
  res.json({ status: "ok", service: "miyabe-tools-mcp" });
});

app.post("/mcp", async (req: Request, res: Response) => {
  const server = createServer();
  const transport = new StreamableHTTPServerTransport({
    sessionIdGenerator: undefined
  });

  try {
    await server.connect(transport);
    res.on("close", () => {
      transport.close();
      server.close();
    });
    await transport.handleRequest(req, res, req.body);
  } catch (error) {
    console.error("Error handling MCP request:", error);
    transport.close();
    server.close();
    if (!res.headersSent) {
      res.status(500).json({
        jsonrpc: "2.0",
        error: { code: -32603, message: "Internal server error" },
        id: null
      });
    }
  }
});

app.get("/mcp", (req, res) => {
  res.status(405).setHeader("Allow", "POST, OPTIONS").json({
    jsonrpc: "2.0",
    error: { code: -32000, message: "Method not allowed." },
    id: null
  });
});

app.delete("/mcp", (req, res) => {
  res.status(405).setHeader("Allow", "POST, OPTIONS").json({
    jsonrpc: "2.0",
    error: { code: -32000, message: "Method not allowed." },
    id: null
  });
});

app.listen(port, "0.0.0.0", () => {
  console.log(`Miyabe Tools MCP server listening on port ${port}`);
});
