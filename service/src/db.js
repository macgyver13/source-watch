import { applyOverlay, renderJsonl, renderRss, weekIndex } from "./overlay.js";

export const CHUNK = 400000;
export const ROW_BATCH = 20;
const RAW_TABLES = { items: "raw_items", projects: "raw_projects", sources: "raw_sources" };

export function nowIso() {
  return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
}

export async function etagOf(body) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(body ?? ""));
  const hex = [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
  return hex.slice(0, 16);
}

export function emptyPayload(name) {
  const generated = nowIso();
  if (name === "feed.json") {
    return JSON.stringify({
      schema_version: "source-watch.feed.v0",
      title: "Source Watch",
      description: "Public-source activity feed.",
      generated_at: generated,
      items: [],
    });
  }
  if (name === "projects.json") {
    return JSON.stringify({ schema_version: "source-watch.projects.v0", projects: [] });
  }
  if (name === "sources.json") {
    return JSON.stringify({ schema_version: "source-watch.sources.v0", sources: [] });
  }
  if (name === "watch.json") {
    return JSON.stringify({
      name: "Source Watch",
      default_tag: "",
      preferred_chips: [],
      hidden_tags: [],
      topics: [],
    });
  }
  if (name === "items.jsonl") return "";
  if (name === "feed.xml") {
    return renderRss([], {
      name: "Source Watch",
      base_url: "https://example.com/",
      description: "Public-source activity feed.",
    });
  }
  if (name === "weeks-index") return "[]";
  return "";
}

export async function writeRendered(env, name, body, contentType) {
  const text = body ?? "";
  const etag = await etagOf(text);
  const bytes = new TextEncoder().encode(text).length;
  const stmts = [env.DB.prepare("DELETE FROM rendered WHERE name = ?").bind(name)];
  if (text.length === 0) {
    stmts.push(env.DB.prepare("INSERT INTO rendered (name, seq, body) VALUES (?, ?, ?)").bind(name, 0, ""));
  } else {
    for (let i = 0, seq = 0; i < text.length; i += CHUNK, seq++) {
      stmts.push(
        env.DB.prepare("INSERT INTO rendered (name, seq, body) VALUES (?, ?, ?)").bind(
          name,
          seq,
          text.slice(i, i + CHUNK),
        ),
      );
    }
  }
  stmts.push(
    env.DB.prepare(
      "INSERT OR REPLACE INTO rendered_meta (name, etag, content_type, bytes, updated_at) VALUES (?, ?, ?, ?, ?)",
    ).bind(name, etag, contentType, bytes, nowIso()),
  );
  await env.DB.batch(stmts);
}

export async function readRenderedExact(env, name) {
  const { results } = await env.DB.prepare("SELECT body FROM rendered WHERE name = ? ORDER BY seq").bind(name).all();
  if (!results || !results.length) return null;
  return results.map((row) => row.body ?? "").join("");
}

function stagedName(name, tag) {
  return tag ? `${name}#${tag}` : name;
}

async function allocateRenderTag(env) {
  const row = await env.DB.prepare(
    `INSERT INTO settings (key, value) VALUES ('render_seq', ?)
     ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(settings.value AS INTEGER) + 1 AS TEXT)
     RETURNING value`,
  ).bind(String(Date.now())).first();
  return `g${row?.value || Date.now()}`;
}


async function dropRenderedTag(env, tag) {
  if (!tag) return;
  await env.DB.batch([
    env.DB.prepare("DELETE FROM rendered WHERE name LIKE ?").bind("%#" + tag),
    env.DB.prepare("DELETE FROM rendered_meta WHERE name LIKE ?").bind("%#" + tag),
  ]);
}


export async function publishLiveRenderTag(env, tag) {
  const result = await env.DB.prepare(
    `INSERT INTO settings (key, value) VALUES ('live_render_tag', ?)
     ON CONFLICT(key) DO UPDATE SET value = excluded.value
     WHERE settings.value < excluded.value`,
  ).bind(tag).run();
  return Boolean(result?.meta?.changes);
}


const RENDER_STALE_MS = 10 * 60 * 1000;

async function purgeStaleRendered(env, liveTag) {
  const cutoff = Date.now() - RENDER_STALE_MS;
  const { results } = await env.DB.prepare(
    "SELECT DISTINCT name FROM rendered WHERE name LIKE '%#g%'",
  ).all();
  const stale = [];
  for (const row of results || []) {
    const name = String(row.name || "");
    const match = name.match(/#g(\d+)$/);
    if (!match) continue;
    if (`g${match[1]}` === liveTag) continue;
    if (Number(match[1]) >= cutoff) continue;
    stale.push(name);
  }
  if (!stale.length) return;
  const stmts = [];
  for (const name of stale) {
    stmts.push(env.DB.prepare("DELETE FROM rendered WHERE name = ?").bind(name));
    stmts.push(env.DB.prepare("DELETE FROM rendered_meta WHERE name = ?").bind(name));
  }
  for (let i = 0; i < stmts.length; i += 40) {
    await env.DB.batch(stmts.slice(i, i + 40));
  }
}


async function liveRenderedName(env, name) {
  const tag = await getSetting(env, "live_render_tag");
  return stagedName(name, tag);
}

export async function readRendered(env, name) {
  const live = await liveRenderedName(env, name);
  const body = await readRenderedExact(env, live);
  if (body != null || live === name) return body;
  return readRenderedExact(env, name);
}

export async function readRenderedMeta(env, name) {
  const live = await liveRenderedName(env, name);
  const meta = await env.DB.prepare(
    "SELECT etag, content_type, bytes, updated_at FROM rendered_meta WHERE name = ?",
  ).bind(live).first();
  if (meta || live === name) return meta;
  return env.DB.prepare(
    "SELECT etag, content_type, bytes, updated_at FROM rendered_meta WHERE name = ?",
  ).bind(name).first();
}


export async function getSetting(env, key) {
  const row = await env.DB.prepare("SELECT value FROM settings WHERE key = ?").bind(key).first();
  return row ? row.value : null;
}

export async function setSetting(env, key, value) {
  await env.DB.prepare("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)").bind(key, String(value ?? "")).run();
}

export async function audit(env, action, target, detail) {
  await env.DB.prepare("INSERT INTO audit_log (at, action, target, detail) VALUES (?, ?, ?, ?)").bind(
    nowIso(),
    action,
    target ?? null,
    detail ?? null,
  ).run();
}

export async function getIngest(env, ingestId) {
  if (!ingestId) return null;
  return env.DB.prepare("SELECT * FROM ingests WHERE ingest_id = ?").bind(ingestId).first();
}

function parseRows(results) {
  const out = [];
  for (const row of results || []) {
    try {
      out.push(JSON.parse(row.json));
    } catch {
      /* skip malformed */
    }
  }
  return out;
}

export async function loadRaw(env, ingestId) {
  if (!ingestId) return { items: [], projects: [], sources: [] };
  const [items, projects, sources] = await Promise.all([
    env.DB.prepare("SELECT json FROM raw_items WHERE ingest_id = ?").bind(ingestId).all(),
    env.DB.prepare("SELECT json FROM raw_projects WHERE ingest_id = ?").bind(ingestId).all(),
    env.DB.prepare("SELECT json FROM raw_sources WHERE ingest_id = ?").bind(ingestId).all(),
  ]);
  return {
    items: parseRows(items.results),
    projects: parseRows(projects.results),
    sources: parseRows(sources.results),
  };
}

export async function loadOverrides(env) {
  const { results } = await env.DB.prepare("SELECT kind, target_id, patch FROM overrides").all();
  const out = { item: {}, project: {}, source: {} };
  for (const row of results || []) {
    if (!out[row.kind]) continue;
    try {
      out[row.kind][row.target_id] = JSON.parse(row.patch);
    } catch {
      out[row.kind][row.target_id] = {};
    }
  }
  return out;
}

export async function loadExclusions(env) {
  const { results } = await env.DB.prepare(
    "SELECT id, kind, value, note, created_at FROM exclusions ORDER BY id",
  ).all();
  return results || [];
}

export async function loadIncludeTerms(env) {
  const { results } = await env.DB.prepare(
    "SELECT id, bucket, term, note, created_at FROM include_terms ORDER BY id",
  ).all();
  return results || [];
}

export async function loadSeedAdditions(env) {
  const { results } = await env.DB.prepare(
    "SELECT id, kind, entry, created_at FROM seed_additions ORDER BY id",
  ).all();
  const out = [];
  for (const row of results || []) {
    let parsed = row.entry;
    try {
      parsed = JSON.parse(row.entry);
    } catch {
      parsed = {};
    }
    out.push({ id: row.id, kind: row.kind, entry: parsed, created_at: row.created_at });
  }
  return out;
}

export async function insertRawRows(env, ingestId, kind, rows) {
  const table = RAW_TABLES[kind];
  if (!table) throw new Error(`unknown kind ${kind}`);
  const valid = (rows || []).filter((row) => row && row.id);
  for (let i = 0; i < valid.length; i += ROW_BATCH) {
    const slice = valid.slice(i, i + ROW_BATCH);
    const placeholders = slice.map(() => "(?,?,?)").join(",");
    const binds = [];
    for (const row of slice) {
      binds.push(ingestId, String(row.id), JSON.stringify(row));
    }
    await env.DB.prepare(`INSERT OR REPLACE INTO ${table} (ingest_id, id, json) VALUES ${placeholders}`)
      .bind(...binds)
      .run();
  }
  return valid.length;
}

export async function renderAll(env) {
  const liveId = await getSetting(env, "live_ingest_id");
  const ingest = await getIngest(env, liveId);
  const raw = await loadRaw(env, liveId);
  const overrides = await loadOverrides(env);
  const exclusions = await loadExclusions(env);
  const overlaid = applyOverlay({
    items: raw.items,
    projects: raw.projects,
    sources: raw.sources,
    overrides,
    exclusions,
  });
  let watch = {};
  if (ingest?.watch_json) {
    try {
      watch = JSON.parse(ingest.watch_json);
    } catch {
      watch = {};
    }
  }
  const feed = {
    schema_version: "source-watch.feed.v0",
    title: ingest?.feed_title || watch.name || "Source Watch",
    description: ingest?.feed_description || "Public-source activity feed.",
    generated_at: ingest?.generated_at || nowIso(),
    items: overlaid.items,
  };
  const projects = { schema_version: "source-watch.projects.v0", projects: overlaid.projects };
  const sources = { schema_version: "source-watch.sources.v0", sources: overlaid.sources };
  const jsonType = "application/json; charset=utf-8";
  const tag = await allocateRenderTag(env);

  const n = (name) => stagedName(name, tag);
  await writeRendered(env, n("feed.json"), JSON.stringify(feed), jsonType);
  await writeRendered(env, n("projects.json"), JSON.stringify(projects), jsonType);
  await writeRendered(env, n("sources.json"), JSON.stringify(sources), jsonType);
  const publicWatch = { ...watch };
  delete publicWatch.base_url;
  await writeRendered(env, n("watch.json"), JSON.stringify(publicWatch), jsonType);
  await writeRendered(env, n("items.jsonl"), renderJsonl(overlaid.items), "application/jsonl; charset=utf-8");
  await writeRendered(
    env,
    n("feed.xml"),
    renderRss(overlaid.items, {
      name: feed.title,
      base_url: watch.base_url || "",
      description: feed.description,
    }),
    "application/rss+xml; charset=utf-8",
  );
  await writeRendered(env, n("weeks-index"), JSON.stringify(weekIndex(overlaid.items)), jsonType);
  const published = await publishLiveRenderTag(env, tag);
  if (!published) {
    await dropRenderedTag(env, tag);
    return {
      items: overlaid.items.length,
      projects: overlaid.projects.length,
      sources: overlaid.sources.length,
    };
  }
  await purgeStaleRendered(env, tag);




  return {
    items: overlaid.items.length,
    projects: overlaid.projects.length,
    sources: overlaid.sources.length,
  };
}

export async function commitIngest(env, ingestId) {
  const row = await getIngest(env, ingestId);
  if (!row) return null;
  const at = nowIso();
  const staleBefore = new Date(Date.now() - 60 * 60 * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
  const staleUncommitted =
    "SELECT ingest_id FROM ingests WHERE committed_at IS NULL AND started_at < ? AND ingest_id != ?";
  const otherCommitted =
    "SELECT ingest_id FROM ingests WHERE committed_at IS NOT NULL AND ingest_id != ?";
  await env.DB.batch([
    env.DB.prepare("UPDATE ingests SET committed_at = ? WHERE ingest_id = ?").bind(at, ingestId),
    env.DB.prepare("INSERT OR REPLACE INTO settings (key, value) VALUES ('live_ingest_id', ?)").bind(ingestId),
    env.DB.prepare("INSERT OR REPLACE INTO settings (key, value) VALUES ('last_ingest_at', ?)").bind(at),
    env.DB.prepare(`DELETE FROM raw_items WHERE ingest_id IN (${otherCommitted})`).bind(ingestId),
    env.DB.prepare(`DELETE FROM raw_projects WHERE ingest_id IN (${otherCommitted})`).bind(ingestId),
    env.DB.prepare(`DELETE FROM raw_sources WHERE ingest_id IN (${otherCommitted})`).bind(ingestId),
    env.DB.prepare(`DELETE FROM raw_items WHERE ingest_id IN (${staleUncommitted})`).bind(staleBefore, ingestId),
    env.DB.prepare(`DELETE FROM raw_projects WHERE ingest_id IN (${staleUncommitted})`).bind(staleBefore, ingestId),
    env.DB.prepare(`DELETE FROM raw_sources WHERE ingest_id IN (${staleUncommitted})`).bind(staleBefore, ingestId),
    env.DB.prepare(
      "DELETE FROM ingests WHERE committed_at IS NULL AND started_at < ? AND ingest_id != ?",
    ).bind(staleBefore, ingestId),
  ]);
  const counts = await renderAll(env);
  return { ingest_id: ingestId, ...counts };
}

