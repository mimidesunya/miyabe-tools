#!/usr/bin/env node

const fs = require("node:fs");
const path = require("node:path");
const zlib = require("node:zlib");

const BASE_URL = "https://www.go2senkyo.com";
const SECRET_PATH = path.resolve(__dirname, "..", "..", "secret.json");

class CookieJar {
  constructor() {
    this.cookies = new Map();
  }

  apply(response) {
    const setCookies = [];
    if (typeof response.headers.getSetCookie === "function") {
      setCookies.push(...response.headers.getSetCookie());
    } else {
      const value = response.headers.get("set-cookie");
      if (value) setCookies.push(...splitSetCookie(value));
    }

    for (const header of setCookies) {
      const pair = header.split(";", 1)[0];
      const index = pair.indexOf("=");
      if (index > 0) this.cookies.set(pair.slice(0, index), pair.slice(index + 1));
    }
  }

  header() {
    return [...this.cookies.entries()].map(([key, value]) => `${key}=${value}`).join("; ");
  }
}

function splitSetCookie(header) {
  return header.split(/,(?=\s*[^;,]+=)/g).map((value) => value.trim()).filter(Boolean);
}

function absoluteUrl(url) {
  return new URL(url, BASE_URL).toString();
}

function loadSecret() {
  const secret = JSON.parse(fs.readFileSync(SECRET_PATH, "utf8"));
  const config = secret.go2senkyo || {};
  if (!config.id || !config.password) {
    throw new Error("secret.json の go2senkyo.id / password を入力してください。");
  }
  return {
    cmsUrl: config.cmsUrl || `${BASE_URL}/cms`,
    id: config.id,
    password: config.password,
  };
}

async function request(jar, url, options = {}) {
  const headers = new Headers(options.headers || {});
  headers.set("user-agent", "miyabe-tools go2senkyo draft helper");
  headers.set("accept", options.accept || "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8");
  const cookie = jar.header();
  if (cookie) headers.set("cookie", cookie);

  const response = await fetch(absoluteUrl(url), {
    ...options,
    headers,
    redirect: "manual",
  });
  jar.apply(response);

  if ([301, 302, 303, 307, 308].includes(response.status)) {
    const location = response.headers.get("location");
    if (!location) return response;
    const method = response.status === 303 ? "GET" : (options.method || "GET");
    return request(jar, location, { method, headers: { referer: absoluteUrl(url) } });
  }

  return response;
}

function htmlDecode(value) {
  return value
    .replace(/&amp;/g, "&")
    .replace(/&quot;/g, "\"")
    .replace(/&#39;/g, "'")
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

function stripTags(value) {
  return htmlDecode(value.replace(/<script[\s\S]*?<\/script>/gi, " ")
    .replace(/<style[\s\S]*?<\/style>/gi, " ")
    .replace(/<[^>]+>/g, " ")
    .replace(/\s+/g, " ")
    .trim());
}

function extractTitle(html) {
  const match = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  return match ? stripTags(match[1]) : "";
}

function extractAuthenticityToken(html) {
  const input = html.match(/<input[^>]+name=["']authenticity_token["'][^>]*>/i);
  if (input) {
    const value = input[0].match(/\bvalue=["']([^"']+)["']/i);
    if (value) return htmlDecode(value[1]);
  }
  const meta = html.match(/<meta[^>]+name=["']csrf-token["'][^>]*>/i);
  if (meta) {
    const value = meta[0].match(/\bcontent=["']([^"']+)["']/i);
    if (value) return htmlDecode(value[1]);
  }
  throw new Error("CSRF token が見つかりません。");
}

function extractLinks(html) {
  const links = [];
  const linkPattern = /<a\b([^>]*)>([\s\S]*?)<\/a>/gi;
  let match;
  while ((match = linkPattern.exec(html))) {
    const href = match[1].match(/\bhref=["']([^"']+)["']/i);
    if (!href) continue;
    const text = stripTags(match[2]);
    if (!text && href[1] === "#") continue;
    links.push({ text, href: htmlDecode(href[1]) });
  }
  return links;
}

function extractImages(html) {
  const images = [];
  const imagePattern = /<img\b([^>]*)>/gi;
  let match;
  while ((match = imagePattern.exec(html))) {
    const attrs = match[1];
    const src = attrs.match(/\bsrc=["']([^"']+)["']/i);
    const alt = attrs.match(/\balt=["']([^"']*)["']/i);
    if (!src) continue;
    images.push({
      src: htmlDecode(src[1]),
      alt: alt ? htmlDecode(alt[1]) : "",
    });
  }
  return images;
}

function extractForms(html) {
  const forms = [];
  const formPattern = /<form\b([^>]*)>([\s\S]*?)<\/form>/gi;
  let match;
  while ((match = formPattern.exec(html))) {
    const attrs = match[1];
    const action = attrs.match(/\baction=["']([^"']+)["']/i);
    const method = attrs.match(/\bmethod=["']([^"']+)["']/i);
    const id = attrs.match(/\bid=["']([^"']+)["']/i);
    const klass = attrs.match(/\bclass=["']([^"']+)["']/i);
    const fields = [];
    const fieldPattern = /<(input|textarea|select)\b([^>]*)>/gi;
    let fieldMatch;
    while ((fieldMatch = fieldPattern.exec(match[2]))) {
      const name = fieldMatch[2].match(/\bname=["']([^"']+)["']/i);
      const type = fieldMatch[2].match(/\btype=["']([^"']+)["']/i);
      if (name) fields.push({ name: htmlDecode(name[1]), type: type ? htmlDecode(type[1]) : fieldMatch[1].toLowerCase() });
    }
    forms.push({
      id: id ? htmlDecode(id[1]) : "",
      className: klass ? htmlDecode(klass[1]) : "",
      action: action ? htmlDecode(action[1]) : "",
      method: method ? htmlDecode(method[1]).toUpperCase() : "GET",
      fields,
    });
  }
  return forms;
}

function extractInputValue(html, name, fallback = "") {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const input = html.match(new RegExp(`<input[^>]+name=["']${escaped}["'][^>]*>`, "i"));
  if (!input) return fallback;
  const value = input[0].match(/\bvalue=["']([^"']*)["']/i);
  return value ? htmlDecode(value[1]) : fallback;
}

function defaultPublishedParts() {
  const now = new Date();
  return {
    date: `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`,
    year: String(now.getFullYear()),
    month: String(now.getMonth() + 1),
    day: String(now.getDate()),
    hour: String(now.getHours()).padStart(2, "0"),
    minute: String(Math.floor(now.getMinutes() / 5) * 5).padStart(2, "0"),
  };
}

async function login() {
  const config = loadSecret();
  const jar = new CookieJar();
  const loginResponse = await request(jar, "/dusers/sign_in");
  const loginHtml = await loginResponse.text();
  const token = extractAuthenticityToken(loginHtml);
  const body = new URLSearchParams({
    authenticity_token: token,
    "devise_user[email]": config.id,
    "devise_user[password]": config.password,
    "devise_user[remember_me]": "0",
    commit: "ログイン",
  });

  const response = await request(jar, "/dusers/sign_in", {
    method: "POST",
    headers: {
      "content-type": "application/x-www-form-urlencoded",
      origin: BASE_URL,
      referer: `${BASE_URL}/dusers/sign_in`,
    },
    body,
  });
  const html = await response.text();
  if (/new_devise_user|devise_user\[password\]|ログイン/.test(html) && !/ログアウト|活動|CMS|管理/.test(html)) {
    throw new Error("ログインに失敗した可能性があります。ID/パスワードまたは追加認証を確認してください。");
  }
  return { jar, html, url: response.url || config.cmsUrl };
}

async function probe() {
  const { jar } = await login();
  const response = await request(jar, "/cms");
  const html = await response.text();
  const links = extractLinks(html);
  const interesting = links.filter((link) => {
    const value = `${link.text} ${link.href}`;
    return /活動|記録|記事|投稿|ブログ|blog|article|post|activity|cms/i.test(value);
  });

  console.log(JSON.stringify({
    ok: true,
    title: extractTitle(html),
    url: response.url,
    interestingLinks: interesting.slice(0, 80),
    formCount: extractForms(html).length,
  }, null, 2));
}

async function inspectPage(url) {
  const { jar } = await login();
  const response = await request(jar, url);
  const html = await response.text();
  const inputs = [];
  const inputPattern = /<(input|textarea|select|button)\b([^>]*)>([\s\S]*?)(?:<\/\1>)?/gi;
  let match;
  while ((match = inputPattern.exec(html))) {
    const attrs = match[2];
    const getAttr = (name) => {
      const found = attrs.match(new RegExp(`\\b${name}=["']([^"']*)["']`, "i"));
      return found ? htmlDecode(found[1]) : "";
    };
    inputs.push({
      tag: match[1].toLowerCase(),
      type: getAttr("type"),
      name: getAttr("name"),
      id: getAttr("id"),
      value: getAttr("value"),
      text: stripTags(match[3] || ""),
    });
  }

  console.log(JSON.stringify({
    ok: true,
    title: extractTitle(html),
    url: response.url,
    forms: extractForms(html),
    links: extractLinks(html).filter((link) => /下書|保存|公開|削除|戻る|活動|記事|投稿/.test(link.text)).slice(0, 80),
    images: extractImages(html).slice(0, 80),
    inputs: inputs.filter((input) => input.name || input.id || input.value || input.text).slice(0, 200),
  }, null, 2));
}

async function createDummyDraft() {
  const { jar } = await login();
  const newPath = "/cms/politicians/6880/posts/new";
  const newResponse = await request(jar, newPath);
  const html = await newResponse.text();
  const token = extractAuthenticityToken(html);
  const form = extractForms(html).find((candidate) => candidate.id === "new_post" || /\/posts$/.test(candidate.action));
  if (!form) throw new Error("活動記録の新規投稿フォームが見つかりません。");

  const parts = defaultPublishedParts();
  const nowLabel = new Date().toLocaleString("ja-JP", { timeZone: "Asia/Tokyo" });
  const title = `【ダミー】活動記録テスト ${nowLabel}`;
  const body = [
    "<p>これは自動投稿テスト用のダミー記事です。</p>",
    "<p>画像付きの活動記録を下書き状態で作成できるか確認するための内容です。確認後に削除してください。</p>",
  ].join("");

  const formData = new FormData();
  formData.append("authenticity_token", token);
  formData.append("post[title]", title);
  formData.append("post[body]", body);
  formData.append("post[is_created_ckeditor5]", extractInputValue(html, "post[is_created_ckeditor5]", "true"));
  formData.append("post[thumbnail]", new Blob([createDummyPng()], { type: "image/png" }), "go2senkyo_dummy.png");
  formData.append("post[remove_thumbnail]", "0");
  formData.append("published_at_dammy", extractInputValue(html, "published_at_dammy", parts.date));
  formData.append("post[published_at(1i)]", extractInputValue(html, "post[published_at(1i)]", parts.year));
  formData.append("post[published_at(2i)]", extractInputValue(html, "post[published_at(2i)]", parts.month));
  formData.append("post[published_at(3i)]", extractInputValue(html, "post[published_at(3i)]", parts.day));
  formData.append("post[published_at(4i)]", parts.hour);
  formData.append("post[published_at(5i)]", parts.minute);
  formData.append("set_published_at", extractInputValue(html, "set_published_at", "no"));
  formData.append("post[state]", "draft");

  const postResponse = await request(jar, form.action, {
    method: "POST",
    headers: {
      origin: BASE_URL,
      referer: absoluteUrl(newPath),
    },
    body: formData,
  });
  const resultHtml = await postResponse.text();
  const draftListResponse = await request(jar, "/cms/politicians/6880/posts?select=draft");
  const draftListHtml = await draftListResponse.text();
  const draftLink = extractLinks(draftListHtml).find((link) => link.text === title);

  if (!draftLink) {
    console.log(JSON.stringify({
      ok: false,
      title,
      url: postResponse.url || "",
      forms: extractForms(resultHtml),
      createdMessagePresent: /活動記録を作成しました|保存しました|更新しました/.test(resultHtml),
      messages: stripTags(resultHtml).slice(0, 2000),
    }, null, 2));
    process.exitCode = 1;
    return;
  }

  const editResponse = await request(jar, draftLink.href);
  const editHtml = await editResponse.text();
  const images = extractImages(editHtml).filter((image) => /uploads\/blogit\/post\/thumbnail/.test(image.src));

  console.log(JSON.stringify({
    ok: true,
    title,
    url: absoluteUrl(draftLink.href),
    stateSubmitted: "draft",
    evidence: {
      createdMessagePresent: /活動記録を作成しました|保存しました|更新しました/.test(resultHtml),
      foundInDraftList: draftListHtml.includes(title),
      containsDraftText: /下書|draft/i.test(draftListHtml),
      editTitleMatches: extractInputValue(editHtml, "post[title]") === title,
      thumbnailCount: images.length,
      thumbnailUrls: images.map((image) => image.src),
    },
  }, null, 2));
}

async function listPosts(url = "/cms/politicians/6880/posts") {
  const { jar } = await login();
  const response = await request(jar, url);
  const html = await response.text();
  const postLinks = [];
  const seen = new Set();
  for (const link of extractLinks(html)) {
    if (!/\/cms\/politicians\/\d+\/posts\/[^/]+\/edit/.test(link.href)) continue;
    if (seen.has(link.href)) continue;
    seen.add(link.href);
    postLinks.push(link);
  }

  const text = stripTags(html);
  const dummyIndex = text.indexOf("【ダミー】");
  console.log(JSON.stringify({
    ok: true,
    url: response.url,
    postLinks: postLinks.slice(0, 40),
    containsDummy: dummyIndex >= 0,
    dummySnippet: dummyIndex >= 0 ? text.slice(Math.max(0, dummyIndex - 120), dummyIndex + 240) : "",
    containsDraftText: /下書|draft/i.test(text),
  }, null, 2));
}

function crc32(buffer) {
  let crc = -1;
  for (const byte of buffer) {
    crc ^= byte;
    for (let bit = 0; bit < 8; bit += 1) {
      crc = (crc >>> 1) ^ (0xedb88320 & -(crc & 1));
    }
  }
  return (crc ^ -1) >>> 0;
}

function pngChunk(type, data) {
  const typeBuffer = Buffer.from(type, "ascii");
  const length = Buffer.alloc(4);
  length.writeUInt32BE(data.length, 0);
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuffer, data])), 0);
  return Buffer.concat([length, typeBuffer, data, crc]);
}

function createDummyPng() {
  const width = 960;
  const height = 540;
  const rows = [];
  for (let y = 0; y < height; y += 1) {
    const row = Buffer.alloc(1 + width * 4);
    row[0] = 0;
    for (let x = 0; x < width; x += 1) {
      const offset = 1 + x * 4;
      const band = Math.floor((x / width) * 255);
      const stripe = Math.floor(y / 36) % 2 === 0 ? 24 : 0;
      row[offset] = 236;
      row[offset + 1] = 72 + Math.floor(band / 5) + stripe;
      row[offset + 2] = 84 + Math.floor((255 - band) / 4);
      row[offset + 3] = 255;
    }
    rows.push(row);
  }

  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;

  return Buffer.concat([
    Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
    pngChunk("IHDR", ihdr),
    pngChunk("IDAT", zlib.deflateSync(Buffer.concat(rows))),
    pngChunk("IEND", Buffer.alloc(0)),
  ]);
}

async function main() {
  const command = process.argv[2] || "probe";
  if (command === "probe") {
    await probe();
    return;
  }
  if (command === "inspect") {
    await inspectPage(process.argv[3] || "/cms/politicians/6880/posts/new");
    return;
  }
  if (command === "dummy-image") {
    const outPath = path.resolve(__dirname, "..", "..", "work", "go2senkyo_dummy.png");
    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    fs.writeFileSync(outPath, createDummyPng());
    console.log(JSON.stringify({ ok: true, path: outPath }, null, 2));
    return;
  }
  if (command === "create-dummy-draft") {
    await createDummyDraft();
    return;
  }
  if (command === "list-posts") {
    await listPosts(process.argv[3] || "/cms/politicians/6880/posts");
    return;
  }
  throw new Error(`Unknown command: ${command}`);
}

main().catch((error) => {
  console.error(error.message);
  if (error.cause) {
    console.error(error.cause.code || error.cause.message || error.cause);
  }
  process.exitCode = 1;
});
