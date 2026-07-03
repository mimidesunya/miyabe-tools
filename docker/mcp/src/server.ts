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
  addOptional(params, "sort", args.sort || "date");
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
  sort: z.enum(["date", "relevance"]).default("date").describe("date は新しい順、relevance は関連度順。"),
  page: z.number().int().min(1).default(1).describe("ページ番号。"),
  per_page: z.number().int().min(1).max(50).default(10).describe("1ページあたりの件数。MCPでは最大50件。"),
  include_facets: z.boolean().optional().describe("true の場合、文書種別・都道府県・自治体の集計も返します。"),
  include_body_highlight: z.boolean().optional().describe("false の場合、本文ハイライト生成を抑制します。")
};

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
      inputSchema: searchInputSchema
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
      inputSchema: searchInputSchema
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
      }
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
