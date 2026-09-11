import { ADMIN_HTML } from "./admin-ui.js";
import * as db from "./db.js";
import { applyItemPatch, applyNamedPatch, applyOverlay, githubRepoFromUrl, matchingExclusion, resolveProjectDisplayNames, seedEntryKeys, seedLocatorTaken, slugify, sourceIdForItem } from "./overlay.js";






const PUBLIC_CACHE = "public, max-age=0, must-revalidate";

const PUBLIC_FILES = {

  "/feed.json": { name: "feed.json", type: "application/json; charset=utf-8" },
  "/projects.json": { name: "projects.json", type: "application/json; charset=utf-8" },
  "/sources.json": { name: "sources.json", type: "application/json; charset=utf-8" },
  "/watch.json": { name: "watch.json", type: "application/json; charset=utf-8" },
  "/items.jsonl": { name: "items.jsonl", type: "application/jsonl; charset=utf-8" },
  "/feed.xml": { name: "feed.xml", type: "application/rss+xml; charset=utf-8" },
};

const OVERRIDE_KINDS = new Set(["item", "project", "source"]);
const EXCLUSION_KINDS = new Set(["term", "url_prefix", "repo", "project", "source_type"]);
const INCLUDE_BUCKETS = new Set(["always_match", "required_any", "context_any"]);
const SEED_KINDS = new Set(["docs_pages", "github_repositories", "github_pull_requests", "crates"]);
const RAW_KINDS = new Set(["items", "projects", "sources"]);



function json(data, status = 200, extra = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", ...extra },
  });
}

function unauthorized() {
  return new Response("unauthorized", {
    status: 401,
    headers: {
      "WWW-Authenticate": "Bearer",
      "Content-Type": "text/plain; charset=utf-8",
    },
  });
}

function timingSafeEqual(a, b) {
  const left = String(a ?? "");
  const right = String(b ?? "");
  const n = Math.max(left.length, right.length);
  let out = left.length ^ right.length;
  for (let i = 0; i < n; i++) {
    out |= (left.charCodeAt(i) || 0) ^ (right.charCodeAt(i) || 0);
  }
  return out === 0;
}

function bearer(request) {
  const header = request.headers.get("Authorization") || "";
  const match = /^Bearer\s+(\S+)/i.exec(header);
  return match ? match[1] : "";
}

function requireToken(request, token) {
  if (!token) return false;
  return timingSafeEqual(bearer(request), token);
}

const ADMIN_FAILS = new Map();
const ADMIN_FAIL_WINDOW_MS = 60_000;
const ADMIN_FAIL_MAX = 10;

function clientIp(request) {
  return (
    request.headers.get("CF-Connecting-IP") ||
    (request.headers.get("X-Forwarded-For") || "").split(",")[0].trim() ||
    "local"
  );
}

function tooManyAuth() {
  return new Response("too many attempts", {
    status: 429,
    headers: {
      "Retry-After": "60",
      "WWW-Authenticate": "Bearer",
      "Content-Type": "text/plain; charset=utf-8",
    },
  });
}

function localNoteAdminFailure(ip) {
  const now = Date.now();
  if (ADMIN_FAILS.size > 1024) {
    for (const [key, row] of ADMIN_FAILS) {
      if (now - row.start >= ADMIN_FAIL_WINDOW_MS) ADMIN_FAILS.delete(key);
    }
  }
  let row = ADMIN_FAILS.get(ip);
  if (!row || now - row.start >= ADMIN_FAIL_WINDOW_MS) row = { start: now, n: 0 };
  row.n += 1;
  ADMIN_FAILS.set(ip, row);
  return row.n <= ADMIN_FAIL_MAX;
}

async function noteAdminAuthFailure(request, env) {
  const ip = clientIp(request);
  const limiter = env.ADMIN_RATE_LIMIT;
  if (limiter && typeof limiter.limit === "function") {
    try {
      const out = await limiter.limit({ key: ip });
      if (out && out.success === false) return false;
      return true;
    } catch {
      /* local fallback */
    }
  }
  return localNoteAdminFailure(ip);
}

async function rejectAdminAuth(request, env) {
  const allowed = await noteAdminAuthFailure(request, env);
  if (!allowed) return tooManyAuth();
  return unauthorized();
}


async function readJson(request) {
  try {
    return await request.json();
  } catch {
    return null;
  }
}

async function serveRendered(request, env, spec) {
  let tag = await db.liveRenderTag(env);
  let meta = await db.readRenderedMetaWithTag(env, spec.name, tag);
  let body = meta ? await db.readRenderedWithTag(env, spec.name, tag) : null;
  if (body == null) {
    const again = await db.liveRenderTag(env);
    if (again && again !== tag) {
      tag = again;
      meta = await db.readRenderedMetaWithTag(env, spec.name, tag);
      body = meta ? await db.readRenderedWithTag(env, spec.name, tag) : null;
    }
  }
  if (body == null) {
    const prev = await db.getSetting(env, "prev_render_tag");
    if (prev) {
      meta = await db.readRenderedMetaWithTag(env, spec.name, prev);
      body = meta ? await db.readRenderedWithTag(env, spec.name, prev) : null;
    }
  }
  const inm = (request.headers.get("If-None-Match") || "").replaceAll('"', "");
  if (meta && body != null && inm && inm === meta.etag) {
    return new Response(null, {
      status: 304,
      headers: {
        ETag: `"${meta.etag}"`,
        "Cache-Control": PUBLIC_CACHE,
      },
    });
  }
  let type = meta?.content_type || spec.type;
  let etag = meta?.etag;
  if (body == null) {
    body = db.emptyPayload(spec.name);
    etag = await db.etagOf(body);
    type = spec.type;
  }

  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": type,
      ETag: `"${etag}"`,
      "Cache-Control": PUBLIC_CACHE,

    },
  });
}

async function serveWeeks(request, env, path) {
  if (path === "/weeks" || path === "/weeks/") {
    const body = await db.readRendered(env, "weeks-index");
    let weeks = [];
    try {
      weeks = body ? JSON.parse(body) : [];
    } catch {
      weeks = [];
    }
    if (!Array.isArray(weeks) || !weeks.length) {
      return env.ASSETS.fetch(request);
    }
    return Response.redirect(new URL(`/weeks/${weeks[0].slug}/`, request.url).toString(), 302);
  }
  const weekMatch = path.match(/^\/weeks\/(\d{4}-W\d{1,2})\/?$/);
  if (weekMatch) {

    const slug = weekMatch[1];
    const padded = slug.replace(/-W(\d)$/, "-W0$1");
    const indexBody = await db.readRendered(env, "weeks-index");
    let weeks = [];
    try {
      weeks = indexBody ? JSON.parse(indexBody) : [];
    } catch {
      weeks = [];
    }
    const known = new Set((Array.isArray(weeks) ? weeks : []).map((row) => row.slug));
    if (!known.has(slug) && !known.has(padded)) {
      return new Response("Not found", { status: 404, headers: { "Cache-Control": PUBLIC_CACHE } });
    }
    const assetReq = new Request(new URL("/weeks/live/", request.url), request);
    const res = await env.ASSETS.fetch(assetReq);
    const headers = new Headers(res.headers);
    headers.set("Cache-Control", PUBLIC_CACHE);
    return new Response(res.body, { status: res.status, statusText: res.statusText, headers });
  }

  return env.ASSETS.fetch(request);
}

function refreshConfigured(env) {
  return Boolean(env.GITHUB_DISPATCH_REPO && env.GITHUB_DISPATCH_TOKEN);
}

export async function dispatchRefresh(env) {
  if (!refreshConfigured(env)) return { configured: false };
  const repo = env.GITHUB_DISPATCH_REPO;
  const workflow = env.GITHUB_DISPATCH_WORKFLOW || "refresh-feed.yml";
  const ref = env.GITHUB_DISPATCH_REF || "main";
  const url = `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "source-watch-service",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref }),
  });
  const text = await res.text();
  return { configured: true, status: res.status, body: text };
}

async function collectorConfig(env) {
  const [exclusions, includeTerms, seedAdditions, discoveredAfter] = await Promise.all([
    db.loadExclusions(env),
    db.loadIncludeTerms(env),
    db.loadSeedAdditions(env),
    db.getSetting(env, "discovered_after"),
  ]);
  return {
    exclusions,
    include_terms: includeTerms,
    settings: discoveredAfter == null ? {} : { discovered_after: discoveredAfter },
    seed_additions: seedAdditions.map((row) => ({ kind: row.kind, entry: row.entry })),
  };
}

async function adminState(env) {
  const liveId = await db.getSetting(env, "live_ingest_id");
  const ingest = await db.getIngest(env, liveId);
  const raw = await db.loadRaw(env, liveId);
  const overrides = await db.loadOverrides(env);
  const exclusions = await db.loadExclusions(env);
  const overlaid = applyOverlay({
    items: raw.items,
    projects: raw.projects,
    sources: raw.sources,
    overrides,
    exclusions,
  });
  return {
    raw: raw.items.length,
    visible: overlaid.items.length,
    hidden: Math.max(0, raw.items.length - overlaid.items.length),
    last_ingest_at: await db.getSetting(env, "last_ingest_at"),
    generated_at: ingest?.generated_at || null,
    refresh_configured: refreshConfigured(env),
  };
}
function visibilityFrom(url) {
  const vis = String(url.searchParams.get("visibility") || "").trim().toLowerCase();
  if (vis) return vis;
  const hidden = url.searchParams.get("hidden");
  if (hidden === "true") return "hidden";
  if (hidden === "false") return "visible";
  return "visible";
}

function pageFrom(url) {
  const limit = Math.min(Math.max(Number(url.searchParams.get("limit")) || 100, 1), 500);
  const offset = Math.max(Number(url.searchParams.get("offset")) || 0, 0);
  return { limit, offset };
}

function inclusionWhy(row) {
  const bits = [];
  const status = row.patch?.status || row.status;
  if (status) bits.push(status);
  if (row.confidence) bits.push(row.confidence);
  const ev = Array.isArray(row.evidence) ? row.evidence[0] : null;
  if (ev?.query) bits.push(`query ${ev.query}`);
  if (ev?.category) bits.push(`category ${ev.category}`);
  if (!bits.length && row.event_type) bits.push(row.event_type);
  return bits.join(" · ");
}

function annotateItem(item, overrides, exclusions, displayNames) {
  const patch = overrides.item[item.id] || null;
  const effective = applyItemPatch({ ...item }, patch);
  const projectId = slugify(item.project);
  const projectPatch = overrides.project[projectId];
  const displayProject = (displayNames && displayNames.get(projectId)) || effective.project || item.project;
  const sourcePatch = overrides.source[sourceIdForItem(item)];
  const rule = matchingExclusion(effective, exclusions, {
    haystack: `${effective.title || ""} ${effective.summary || ""} ${effective.source_url || ""} ${(effective.tags || []).join(" ")}`,
    url: effective.source_url,
    project: [item.project, displayProject],
    sourceType: effective.source_type,
  });
  const hidden = Boolean(patch && patch.hidden);
  const projectHidden = Boolean(projectPatch && projectPatch.hidden);
  const sourceHidden = Boolean(sourcePatch && sourcePatch.hidden);
  const excluded = Boolean(rule);
  const whyHidden = [];
  if (hidden) whyHidden.push("hidden override");
  if (projectHidden) whyHidden.push("project hidden");
  if (sourceHidden) whyHidden.push("source hidden");
  if (rule) whyHidden.push(`excluded ${rule.kind} ${rule.value}`);
  return {
    ...item,
    project: displayProject,
    patch,
    hidden,
    excluded,
    project_hidden: projectHidden,
    source_hidden: sourceHidden,
    suppressed: hidden || excluded || projectHidden || sourceHidden,
    exclusion: rule ? { kind: rule.kind, value: rule.value, note: rule.note || "" } : null,
    why: whyHidden.length ? whyHidden.join(" · ") : inclusionWhy({ ...item, patch }),
  };
}

function annotateNamed(row, kind, overrides, exclusions, displayNames) {
  const patch = overrides[kind][row.id] || null;
  const effective = applyNamedPatch({ ...row }, patch, "name");
  const projectPatch = kind === "source" ? overrides.project[slugify(row.project)] : kind === "project" ? patch : null;
  const displayProject = kind === "project"
    ? ((displayNames && displayNames.get(row.id)) || row.name)
    : ((displayNames && displayNames.get(slugify(row.project))) || row.project);
  const rule = matchingExclusion(effective, exclusions, {
    haystack: `${effective.name || ""} ${effective.url || ""}`,
    url: effective.url,
    project: kind === "project" ? [row.name, row.id, displayProject] : [row.project, displayProject],
    sourceType: effective.source_type,
  });
  const hidden = Boolean(patch && patch.hidden);
  const excluded = Boolean(rule);
  const projectHidden = Boolean(kind !== "project" && projectPatch && projectPatch.hidden);
  const whyHidden = [];
  if (hidden) whyHidden.push("hidden override");
  if (projectHidden) whyHidden.push("project hidden");
  if (rule) whyHidden.push(`excluded ${rule.kind} ${rule.value}`);
  return {
    ...row,
    name: kind === "project" ? displayProject : row.name,
    project: kind === "source" ? displayProject : row.project,
    patch,
    hidden,
    excluded,
    project_hidden: projectHidden,
    suppressed: hidden || excluded || projectHidden,
    exclusion: rule ? { kind: rule.kind, value: rule.value, note: rule.note || "" } : null,
    why: whyHidden.length ? whyHidden.join(" · ") : inclusionWhy({ ...row, patch }),
  };
}



function applyVisibility(rows, visibility) {
  if (visibility === "all") return rows;
  if (visibility === "excluded") return rows.filter((row) => row.excluded);
  if (visibility === "hidden") return rows.filter((row) => row.suppressed);
  return rows.filter((row) => !row.suppressed);
}

function applyQuery(rows, q, fields) {
  if (!q) return rows;
  return rows.filter((row) => {
    if (fields.some((field) => String(row[field] || "").toLowerCase().includes(q))) return true;
    const patch = row.patch;
    if (!patch || typeof patch !== "object") return false;
    return fields.some((field) => String(patch[field] || "").toLowerCase().includes(q));
  });
}


async function adminItems(env, url) {
  const q = (url.searchParams.get("q") || "").toLowerCase();
  const status = url.searchParams.get("status") || "";
  const visibility = visibilityFrom(url);
  const { limit, offset } = pageFrom(url);
  const liveId = await db.getSetting(env, "live_ingest_id");
  const raw = await db.loadRaw(env, liveId);
  const overrides = await db.loadOverrides(env);
  const exclusions = await db.loadExclusions(env);
  const displayNames = resolveProjectDisplayNames(raw.projects, overrides.project);
  let rows = raw.items.map((item) => annotateItem(item, overrides, exclusions, displayNames));

  rows = applyQuery(rows, q, ["title", "summary", "source_url", "id", "project"]);
  if (status) {
    rows = rows.filter((item) => (item.patch?.status || item.status) === status);
  }
  rows = applyVisibility(rows, visibility);
  const total = rows.length;
  return { items: rows.slice(offset, offset + limit), total, limit, offset };
}

async function adminKindList(env, kind, url) {
  const q = (url.searchParams.get("q") || "").toLowerCase();
  const visibility = visibilityFrom(url);
  const { limit, offset } = pageFrom(url);
  const liveId = await db.getSetting(env, "live_ingest_id");
  const raw = await db.loadRaw(env, liveId);
  const overrides = await db.loadOverrides(env);
  const exclusions = await db.loadExclusions(env);
  const key = kind === "projects" ? "project" : "source";
  const displayNames = resolveProjectDisplayNames(raw.projects, overrides.project);
  const overlaid = applyOverlay({
    items: raw.items,
    projects: raw.projects,
    sources: raw.sources,
    overrides,
    exclusions,
  });
  const publicIds = new Set(
    (kind === "projects" ? overlaid.projects : overlaid.sources).map((row) => row.id),
  );
  let rows = (kind === "projects" ? raw.projects : raw.sources).map((row) => {
    const annotated = annotateNamed(row, key, overrides, exclusions, displayNames);
    if (annotated.suppressed || publicIds.has(row.id)) return annotated;
    return {
      ...annotated,
      suppressed: true,
      why: kind === "projects" ? "no visible items" : "hidden with project or items",
    };
  });
  rows = applyQuery(rows, q, ["name", "url", "id", "project"]);
  rows = applyVisibility(rows, visibility);
  const total = rows.length;
  const sliced = rows.slice(offset, offset + limit);
  return kind === "projects"
    ? { projects: sliced, total, limit, offset }
    : { sources: sliced, total, limit, offset };
}


async function rowRef(env, kind, id) {
  const liveId = await db.getSetting(env, "live_ingest_id");
  const raw = await db.loadRaw(env, liveId);
  const list = kind === "item" ? raw.items : kind === "project" ? raw.projects : raw.sources;
  const row = (list || []).find((r) => r.id === id) || {};
  return {
    id,
    title: row.title || row.name || id,
    url: row.source_url || row.url || "",
  };
}

function auditTarget(ref) {
  return ref.url || ref.title || ref.id;
}

async function ensureRendered(env) {
  try {
    const counts = await db.renderUntilPublished(env);
    if (counts.published === false) return json({ error: "render_not_published" }, 503);
    return null;
  } catch (err) {
    return json({ error: "render_failed", detail: String(err && err.message || err).slice(0, 200) }, 503);
  }
}



async function putOverride(env, kind, id, patch) {
  const existingRow = await env.DB.prepare("SELECT patch, updated_at, revision FROM overrides WHERE kind = ? AND target_id = ?")
    .bind(kind, id)
    .first();
  let current = {};
  if (existingRow?.patch) {
    try {
      current = JSON.parse(existingRow.patch);
    } catch {
      current = {};
    }
  }
  const next = { ...current };
  for (const [key, value] of Object.entries(patch || {})) {
    if (value === null || (key === "title" && typeof value === "string" && !value.trim())) delete next[key];
    else next[key] = value;
  }

  const written = JSON.stringify(next);
  const writeRevision = crypto.randomUUID();
  const writeRes = existingRow
    ? await env.DB.prepare(
        "UPDATE overrides SET patch = ?, updated_at = ?, revision = ? WHERE kind = ? AND target_id = ? AND revision = ?",
      )
        .bind(written, db.nowIso(), writeRevision, kind, id, existingRow.revision)
        .run()
    : await env.DB.prepare(
        "INSERT INTO overrides (kind, target_id, patch, updated_at, revision) VALUES (?, ?, ?, ?, ?) ON CONFLICT(kind, target_id) DO NOTHING",
      )
        .bind(kind, id, written, db.nowIso(), writeRevision)
        .run();
  if (!writeRes?.meta?.changes) return json({ error: "override_conflict" }, 409);
  const fail = await ensureRendered(env);
  if (fail) {
    if (!existingRow) {
      await env.DB.prepare("DELETE FROM overrides WHERE kind = ? AND target_id = ? AND revision = ?")
        .bind(kind, id, writeRevision)
        .run();
    } else {
      await env.DB.prepare(
        "UPDATE overrides SET patch = ?, updated_at = ?, revision = ? WHERE kind = ? AND target_id = ? AND revision = ?",
      )
        .bind(existingRow.patch, existingRow.updated_at, crypto.randomUUID(), kind, id, writeRevision)
        .run();
    }
    return fail;
  }

  const ref = await rowRef(env, kind, id);
  await db.audit(env, "override_put", auditTarget(ref), JSON.stringify({ ...ref, patch: next }));
  return next;
}




async function handleCollector(request, env, path) {
  if (!requireToken(request, env.INGEST_TOKEN)) return unauthorized();
  const method = request.method;
  if (method === "GET" && path === "/api/collector/config") {
    return json(await collectorConfig(env));
  }
  if (method === "GET" && path === "/api/collector/state") {
    for (let attempt = 0; attempt < 2; attempt++) {
      const liveId = await db.getSetting(env, "live_ingest_id");
      const raw = await db.loadRaw(env, liveId);
      const still = await db.getSetting(env, "live_ingest_id");
      if (still === liveId) return json(raw);
    }
    return json({ error: "ingest_changed" }, 409);
  }

  if (method === "POST" && path === "/api/ingest/begin") {
    const body = await readJson(request);
    if (!body || typeof body !== "object") return json({ error: "invalid_json" }, 400);
    const ingestId = crypto.randomUUID();
    await env.DB.prepare(
      "INSERT INTO ingests (ingest_id, started_at, generated_at, watch_json, feed_title, feed_description) VALUES (?, ?, ?, ?, ?, ?)",
    )
      .bind(
        ingestId,
        db.nowIso(),
        body.generated_at || null,
        JSON.stringify(body.watch || {}),
        body.feed_title || null,
        body.feed_description || null,
      )
      .run();
    return json({ ingest_id: ingestId });
  }
  if (method === "POST" && path === "/api/ingest/chunk") {
    const body = await readJson(request);
    if (!body || typeof body !== "object") return json({ error: "invalid_json" }, 400);
    const ingestId = String(body.ingest_id || "");
    const kind = String(body.kind || "");
    if (!ingestId || !RAW_KINDS.has(kind)) return json({ error: "invalid_chunk" }, 400);
    const ingest = await db.getIngest(env, ingestId);
    if (!ingest) return json({ error: "unknown_ingest" }, 409);
    if (ingest.committed_at) return json({ error: "already_committed" }, 409);
    const rows = Array.isArray(body.rows) ? body.rows : [];
    if (rows.length > 500) return json({ error: "chunk_too_large" }, 400);
    const result = await db.insertRawRows(env, ingestId, kind, rows);
    if (result.closed) return json({ error: "already_committed" }, 409);
    return json({ written: result.written });
  }
  if (method === "POST" && path === "/api/ingest/commit") {
    const body = await readJson(request);
    if (!body || typeof body !== "object") return json({ error: "invalid_json" }, 400);
    const ingestId = String(body.ingest_id || "");
    let result;
    try {
      result = await db.commitIngest(env, ingestId);
    } catch (err) {
      return json({ error: "commit_failed", detail: String(err && err.message || err).slice(0, 200) }, 503);
    }
    if (!result) return json({ error: "unknown_ingest" }, 409);
    if (result.error === "already_committed" || result.error === "stale_ingest") {
      return json({ error: result.error }, 409);
    }
    if (result.published === false) {
      return json({ error: "render_not_published", ingest_id: result.ingest_id }, 503);
    }
    return json(result);
  }

  return json({ error: "not_found" }, 404);
}

async function handleAdmin(request, env, path, url) {
  if (!requireToken(request, env.ADMIN_TOKEN)) return rejectAdminAuth(request, env);

  const method = request.method;
  if (method === "GET" && path === "/api/admin/state") return json(await adminState(env));
  if (method === "GET" && path === "/api/admin/items") return json(await adminItems(env, url));
  if (method === "GET" && path === "/api/admin/projects") return json(await adminKindList(env, "projects", url));
  if (method === "GET" && path === "/api/admin/sources") return json(await adminKindList(env, "sources", url));



  const overrideMatch = path.match(/^\/api\/admin\/overrides\/([^/]+)\/(.+)$/);
  if (overrideMatch) {
    const kind = decodeURIComponent(overrideMatch[1]);
    const id = decodeURIComponent(overrideMatch[2]);
    if (!OVERRIDE_KINDS.has(kind) || !id) return json({ error: "invalid_override" }, 400);
    if (method === "PUT") {
      const body = await readJson(request);
      if (!body || typeof body !== "object") return json({ error: "invalid_json" }, 400);
      const patch = await putOverride(env, kind, id, body);
      if (patch instanceof Response) return patch;
      return json({ kind, id, patch });
    }
    if (method === "DELETE") {
      const existing = await env.DB.prepare("SELECT patch, updated_at, revision FROM overrides WHERE kind = ? AND target_id = ?")
        .bind(kind, id)
        .first();
      const ref = await rowRef(env, kind, id);
      const deleted = existing
        ? await env.DB.prepare("DELETE FROM overrides WHERE kind = ? AND target_id = ? AND revision = ?")
            .bind(kind, id, existing.revision)
            .run()
        : { meta: { changes: 0 } };
      if (existing && !deleted?.meta?.changes) return json({ error: "override_conflict" }, 409);
      const fail = await ensureRendered(env);
      if (fail) {
        if (existing) {
          await env.DB.prepare(
            "INSERT INTO overrides (kind, target_id, patch, updated_at, revision) VALUES (?, ?, ?, ?, ?) ON CONFLICT(kind, target_id) DO NOTHING",
          )
            .bind(kind, id, existing.patch, existing.updated_at || db.nowIso(), crypto.randomUUID())
            .run();
        }
        return fail;
      }

      await db.audit(env, "override_delete", auditTarget(ref), JSON.stringify(ref));
      return json({ ok: true });
    }
  }

  if (path === "/api/admin/exclusions") {
    if (method === "GET") return json({ exclusions: await db.loadExclusions(env) });
    if (method === "POST") {
      const body = await readJson(request);
      const kind = String(body?.kind || "").trim();
      const value = String(body?.value || "").trim();
      if (!EXCLUSION_KINDS.has(kind) || !value) return json({ error: "invalid_exclusion" }, 400);
      const exclusionRevision = crypto.randomUUID();
      const inserted = await env.DB.prepare(
        "INSERT OR IGNORE INTO exclusions (kind, value, note, created_at, revision) VALUES (?, ?, ?, ?, ?)",
      )
        .bind(kind, value, body?.note ? String(body.note) : null, db.nowIso(), exclusionRevision)
        .run();
      if (!inserted?.meta?.changes) return json({ error: "duplicate_exclusion" }, 409);
      const fail = await ensureRendered(env);
      if (fail) {
        if (inserted?.meta?.changes) {
          await env.DB.prepare("DELETE FROM exclusions WHERE revision = ?")
            .bind(exclusionRevision)
            .run();
        }
        return fail;
      }
      await db.audit(env, "exclusion_add", `${kind}:${value}`, body?.note || null);
      return json({ ok: true });


    }
  }
  const exclusionDel = path.match(/^\/api\/admin\/exclusions\/(\d+)$/);
  if (exclusionDel && method === "DELETE") {
    const exclusionId = Number(exclusionDel[1]);
    const existing = await env.DB.prepare(
      "SELECT id, kind, value, note, created_at, revision FROM exclusions WHERE id = ?",
    ).bind(exclusionId).first();
    const deleted = existing
      ? await env.DB.prepare("DELETE FROM exclusions WHERE id = ? AND revision = ?")
          .bind(exclusionId, existing.revision)
          .run()
      : { meta: { changes: 0 } };
    if (existing && !deleted?.meta?.changes) return json({ error: "exclusion_conflict" }, 409);
    const fail = await ensureRendered(env);
    if (fail) {
      if (existing) {
        await env.DB.prepare(
          "INSERT INTO exclusions (id, kind, value, note, created_at, revision) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING",
        )
          .bind(existing.id, existing.kind, existing.value, existing.note, existing.created_at, crypto.randomUUID())
          .run();
      }
      return fail;
    }
    await db.audit(env, "exclusion_delete", exclusionDel[1], null);
    return json({ ok: true });
  }



  if (path === "/api/admin/include-terms") {
    if (method === "GET") return json({ include_terms: await db.loadIncludeTerms(env) });
    if (method === "POST") {
      const body = await readJson(request);
      const bucket = String(body?.bucket || "").trim();
      const term = String(body?.term || "").trim();
      if (!INCLUDE_BUCKETS.has(bucket) || !term) return json({ error: "invalid_term" }, 400);
      const res = await env.DB.prepare(
        "INSERT OR IGNORE INTO include_terms (bucket, term, note, created_at) VALUES (?, ?, ?, ?)",
      )
        .bind(bucket, term, body?.note ? String(body.note) : null, db.nowIso())
        .run();
      if (!res?.meta?.changes) return json({ error: "duplicate_term" }, 409);
      await db.audit(env, "include_term_add", `${bucket}:${term}`, body?.note || null);
      return json({ ok: true });
    }
  }
  const termDel = path.match(/^\/api\/admin\/include-terms\/(\d+)$/);
  if (termDel && method === "DELETE") {
    await env.DB.prepare("DELETE FROM include_terms WHERE id = ?").bind(Number(termDel[1])).run();
    await db.audit(env, "include_term_delete", termDel[1], null);
    return json({ ok: true });
  }

  if (path === "/api/admin/seed-additions") {
    if (method === "GET") return json({ seed_additions: await db.loadSeedAdditions(env) });
    if (method === "POST") {
      const body = await readJson(request);
      const kind = String(body?.kind || "").trim();
      let entry = body?.entry;
      if (!SEED_KINDS.has(kind) || !entry || typeof entry !== "object" || Array.isArray(entry)) {
        return json({ error: "invalid_seed" }, 400);
      }
      const url = String(entry.url || "").trim();
      let repo = String(entry.repo || "").trim();
      const name = String(entry.name || "").trim();
      if (kind === "github_repositories") {
        if (!/^[^/\s]+\/[^/\s]+$/.test(repo)) repo = githubRepoFromUrl(url);
        if (repo) entry = { ...entry, repo, url: `https://github.com/${repo}` };
      }
      const hasLocator =
        kind === "docs_pages" ? Boolean(url)
        : kind === "github_repositories" ? /^[^/\s]+\/[^/\s]+$/.test(String(entry.repo || ""))
        : kind === "github_pull_requests" ? /^https:\/\/github\.com\/[^/]+\/[^/]+\/pull\/\d+\/?$/i.test(url)
        : kind === "crates" ? Boolean(name) || /^https:\/\/crates\.io\/crates\/[^/]+\/?$/i.test(url)
        : Boolean(url || repo);
      const seedId = String(entry.id || "").trim().toLowerCase();
      if (!seedId || !hasLocator) return json({ error: "invalid_seed" }, 400);
      const existingSeeds = await db.loadSeedAdditions(env);
      if (existingSeeds.some((row) => String(row.entry?.id || "").trim().toLowerCase() === seedId)) {
        return json({ error: "duplicate_seed_id" }, 409);
      }
      const liveId = await db.getSetting(env, "live_ingest_id");
      const raw = await db.loadRaw(env, liveId);
      const catalogIds = new Set();
      for (const row of [...raw.items, ...raw.sources, ...raw.projects]) {
        const rid = String(row.id || "").trim().toLowerCase();
        if (!rid) continue;
        catalogIds.add(rid);
        if (rid.startsWith("seed:")) catalogIds.add(rid.slice(5));
      }
      if (catalogIds.has(seedId) || catalogIds.has(`seed:${seedId}`)) {
        return json({ error: "duplicate_seed_id" }, 409);
      }
      if (seedLocatorTaken(kind, entry, existingSeeds, raw.sources)) {
        return json({ error: "duplicate_seed_locator" }, 409);
      }



      const entryId = seedId;
      const locators = [...seedEntryKeys(kind, entry)];
      try {
        await env.DB.batch([
          env.DB.prepare("INSERT INTO seed_additions (kind, entry, created_at, entry_id) VALUES (?, ?, ?, ?)")
            .bind(kind, JSON.stringify(entry), db.nowIso(), entryId),
          ...locators.map((locator) =>
            env.DB.prepare("INSERT INTO seed_locators (kind, locator, entry_id) VALUES (?, ?, ?)").bind(kind, locator, entryId)),
        ]);
      } catch (err) {
        const msg = String(err && err.message || err);
        return json({ error: msg.includes("seed_locators") ? "duplicate_seed_locator" : "duplicate_seed_id" }, 409);
      }
      await db.audit(env, "seed_add", kind, JSON.stringify(entry));
      return json({ ok: true });
    }
  }
  const seedDel = path.match(/^\/api\/admin\/seed-additions\/(\d+)$/);
  if (seedDel && method === "DELETE") {
    const seedId = Number(seedDel[1]);
    await env.DB.batch([
      env.DB.prepare("DELETE FROM seed_locators WHERE entry_id = (SELECT entry_id FROM seed_additions WHERE id = ?)").bind(seedId),
      env.DB.prepare("DELETE FROM seed_additions WHERE id = ?").bind(seedId),
    ]);
    await db.audit(env, "seed_delete", seedDel[1], null);
    return json({ ok: true });
  }

  if (path === "/api/admin/settings") {
    if (method === "GET") {
      return json({ discovered_after: (await db.getSetting(env, "discovered_after")) || "" });
    }
    if (method === "PUT") {
      const body = await readJson(request);
      if (!body || typeof body !== "object") return json({ error: "invalid_json" }, 400);
      const value = body.discovered_after == null ? "" : String(body.discovered_after);
      await db.setSetting(env, "discovered_after", value);
      await db.audit(env, "settings_put", "discovered_after", value);
      return json({ discovered_after: value });
    }
  }

  if (method === "GET" && path === "/api/admin/audit") {
    const { limit, offset } = pageFrom(url);
    const countRow = await env.DB.prepare("SELECT COUNT(*) AS n FROM audit_log").first();
    const total = Number(countRow?.n || 0);
    const { results } = await env.DB.prepare(
      "SELECT id, at, action, target, detail FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
    )
      .bind(limit, offset)
      .all();
    return json({ audit: results || [], total, limit, offset });
  }

  if (method === "POST" && path === "/api/admin/refresh") {
    const result = await dispatchRefresh(env);
    if (!result.configured) return json({ error: "refresh_not_configured" }, 501);
    if (result.status !== 204) {
      await db.audit(env, "refresh_failed", env.GITHUB_DISPATCH_REPO, `${result.status} ${result.body}`.slice(0, 500));
      return json({ error: "dispatch_failed", status: result.status, body: result.body }, 502);
    }
    await db.audit(env, "refresh_dispatched", env.GITHUB_DISPATCH_REPO, null);
    return json({ dispatched: true }, 202);
  }

  return json({ error: "not_found" }, 404);
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    if (request.method === "GET" && (path === "/admin" || path === "/admin/")) {
      return new Response(ADMIN_HTML, {
        headers: { "Content-Type": "text/html; charset=utf-8" },
      });
    }

    if (PUBLIC_FILES[path]) {
      if (request.method !== "GET" && request.method !== "HEAD") {
        return json({ error: "method_not_allowed" }, 405);
      }
      return serveRendered(request, env, PUBLIC_FILES[path]);
    }

    if (path === "/weeks" || path.startsWith("/weeks/")) {
      return serveWeeks(request, env, path);
    }

    if (path.startsWith("/api/collector/") || path.startsWith("/api/ingest/")) {
      return handleCollector(request, env, path);
    }
    if (path.startsWith("/api/admin/")) {
      return handleAdmin(request, env, path, url);
    }

    return env.ASSETS.fetch(request);
  },

  async scheduled(controller, env) {
    const result = await dispatchRefresh(env);
    if (!result.configured) {
      await db.audit(env, "refresh_skipped", null, "refresh_not_configured");
      return;
    }
    await db.audit(
      env,
      result.status === 204 ? "refresh_dispatched" : "refresh_failed",
      env.GITHUB_DISPATCH_REPO,
      String(result.status),
    );
  },
};
