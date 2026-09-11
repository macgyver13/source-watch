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
  if (name === "feed.json") {
    return JSON.stringify({
      schema_version: "source-watch.feed.v0",
      title: "Source Watch",
      description: "Public-source activity feed.",
      generated_at: "1970-01-01T00:00:00Z",
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
    for (let i = 0, seq = 0; i < text.length; seq++) {
      let end = Math.min(i + CHUNK, text.length);
      if (end < text.length) {
        const code = text.charCodeAt(end - 1);
        if (code >= 0xd800 && code <= 0xdbff) end -= 1; // never split a surrogate pair
      }
      stmts.push(env.DB.prepare("INSERT INTO rendered (name, seq, body) VALUES (?, ?, ?)").bind(name, seq, text.slice(i, end)));
      i = end;
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

async function liveRenderSeq(env) {
  const packed = await getSetting(env, "live_render");
  if (!packed) return 0;
  try {
    return Number(JSON.parse(packed).seq) || 0;
  } catch {
    return 0;
  }
}

async function allocateRenderTag(env) {
  const started = Date.now();
  const floor = (await liveRenderSeq(env)) + 1;
  const row = await env.DB.prepare(
    `INSERT INTO settings (key, value) VALUES ('render_seq', ?)
     ON CONFLICT(key) DO UPDATE SET value = CAST(
       MAX(CAST(settings.value AS INTEGER) + 1, CAST(excluded.value AS INTEGER)) AS TEXT)
     RETURNING value`,
  ).bind(String(floor)).first();
  const seq = Math.max(Number(row?.value || floor), floor);
  const tag = `g${seq}`;
  await setSetting(env, `render_started:${tag}`, String(started));
  return { tag, seq, started };
}


async function dropRenderedTag(env, tag) {
  if (!tag) return;
  await env.DB.batch([
    env.DB.prepare("DELETE FROM rendered WHERE name LIKE ?").bind("%#" + tag),
    env.DB.prepare("DELETE FROM rendered_meta WHERE name LIKE ?").bind("%#" + tag),
    env.DB.prepare("DELETE FROM settings WHERE key = ?").bind(`render_started:${tag}`),
  ]);
}

export async function publishLiveRenderTag(env, tag, seq) {
  const prev = await liveRenderTag(env);
  const payload = JSON.stringify({ seq: Number(seq), tag: String(tag) });
  const result = await env.DB.prepare(
    `INSERT INTO settings (key, value) VALUES ('live_render', ?)
     ON CONFLICT(key) DO UPDATE SET value = excluded.value
     WHERE json_extract(settings.value, '$.seq') IS NULL
        OR CAST(json_extract(settings.value, '$.seq') AS INTEGER)
         < CAST(json_extract(excluded.value, '$.seq') AS INTEGER)`,
  ).bind(payload).run();

  if (!result?.meta?.changes) return false;
  if (prev && prev !== tag) await bestEffort(env, "publish_bookkeeping_failed", () => setSetting(env, "prev_render_tag", prev));
  return true;
}

const RENDER_STALE_MS = 10 * 60 * 1000;

async function purgeStaleRendered(env, liveTag) {
  const cutoff = Date.now() - RENDER_STALE_MS;
  const prev = await getSetting(env, "prev_render_tag");
  const { results } = await env.DB.prepare(
    "SELECT key, value FROM settings WHERE key LIKE 'render_started:%'",
  ).all();
  for (const row of results || []) {
    const tag = String(row.key || "").slice("render_started:".length);
    if (!tag || tag === liveTag || tag === prev) continue;
    if (Number(row.value) >= cutoff) continue;
    await dropRenderedTag(env, tag);
  }
}


export async function liveRenderTag(env) {
  const packed = await getSetting(env, "live_render");
  if (packed) {
    try {
      const parsed = JSON.parse(packed);
      if (parsed && typeof parsed.tag === "string" && parsed.tag) return parsed.tag;
    } catch {
      /* fall through */
    }
  }
  return getSetting(env, "live_render_tag");
}


export async function readRenderedWithTag(env, name, tag) {
  const live = stagedName(name, tag);
  const body = await readRenderedExact(env, live);
  if (body != null || live === name) return body;
  return readRenderedExact(env, name);
}

export async function readRenderedMetaWithTag(env, name, tag) {
  const live = stagedName(name, tag);
  const meta = await env.DB.prepare(
    "SELECT etag, content_type, bytes, updated_at FROM rendered_meta WHERE name = ?",
  ).bind(live).first();
  if (meta || live === name) return meta;
  return env.DB.prepare(
    "SELECT etag, content_type, bytes, updated_at FROM rendered_meta WHERE name = ?",
  ).bind(name).first();
}

export async function readRendered(env, name) {
  return readRenderedWithTag(env, name, await liveRenderTag(env));
}

export async function readRenderedMeta(env, name) {
  return readRenderedMetaWithTag(env, name, await liveRenderTag(env));
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

async function bestEffort(env, action, fn) {
  try {
    await fn();
  } catch (err) {
    try {
      await audit(env, action, null, String(err && err.message || err).slice(0, 200));
    } catch {
      /* ignore */
    }
  }
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
  let written = 0;
  for (let i = 0; i < valid.length; i += ROW_BATCH) {
    const slice = valid.slice(i, i + ROW_BATCH);
    const values = slice.map(() => "(?,?,?)").join(",");
    const binds = [];
    for (const row of slice) binds.push(ingestId, String(row.id), JSON.stringify(row));
    const res = await env.DB.prepare(
      `INSERT OR REPLACE INTO ${table} (ingest_id, id, json)
       SELECT column1, column2, column3 FROM (VALUES ${values})
       WHERE EXISTS (SELECT 1 FROM ingests WHERE ingest_id = ? AND committed_at IS NULL)`,
    ).bind(...binds, ingestId).run();
    if (!res?.meta?.changes) return { written, closed: true };
    written += res.meta.changes;
  }
  return { written, closed: false };
}

export async function renderAll(env, attempt = 0) {
  const { tag, seq } = await allocateRenderTag(env);
  const liveId = await getSetting(env, "live_ingest_id");
  const ingest = await getIngest(env, liveId);
  const raw = await loadRaw(env, liveId);
  const overrides = await loadOverrides(env);
  const exclusions = await loadExclusions(env);
  const stillLive = await getSetting(env, "live_ingest_id");
  if (stillLive !== liveId) {
    await dropRenderedTag(env, tag);
    if (attempt >= 1) return { items: 0, projects: 0, sources: 0, published: false };

    return renderAll(env, attempt + 1);
  }
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
  const published = await publishLiveRenderTag(env, tag, seq);
  const counts = {
    items: overlaid.items.length,
    projects: overlaid.projects.length,
    sources: overlaid.sources.length,
    published,
  };
  if (!published) {
    await dropRenderedTag(env, tag);
    return counts;
  }
  await bestEffort(env, "purge_stale_rendered_failed", () => purgeStaleRendered(env, tag));
  return counts;
}

export async function renderUntilPublished(env, attempts = 3) {
  let last = { items: 0, projects: 0, sources: 0, published: false };
  for (let i = 0; i < attempts; i++) {
    last = await renderAll(env);
    if (last.published !== false) return { ...last, published: true };
  }
  return last;
}


const LIVE_STAMP = "(SELECT COALESCE(generated_at, started_at) FROM ingests WHERE ingest_id = ";

function staleUncommittedCleanup(env, staleBefore, ingestId) {
  const staleUncommitted =
    "SELECT ingest_id FROM ingests WHERE committed_at IS NULL AND started_at < ? AND ingest_id != ?";
  return [
    env.DB.prepare(`DELETE FROM raw_items WHERE ingest_id IN (${staleUncommitted})`).bind(staleBefore, ingestId),
    env.DB.prepare(`DELETE FROM raw_projects WHERE ingest_id IN (${staleUncommitted})`).bind(staleBefore, ingestId),
    env.DB.prepare(`DELETE FROM raw_sources WHERE ingest_id IN (${staleUncommitted})`).bind(staleBefore, ingestId),
    env.DB.prepare(
      "DELETE FROM ingests WHERE committed_at IS NULL AND started_at < ? AND ingest_id != ?",
    ).bind(staleBefore, ingestId),
  ];
}

function previousGenerationCleanup(env, ingestId) {
  const otherCommitted =
    "SELECT ingest_id FROM ingests WHERE committed_at IS NOT NULL AND ingest_id != ?";
  const liveGuard = "(SELECT value FROM settings WHERE key = 'live_ingest_id') = ?";
  return [
    env.DB.prepare(`DELETE FROM raw_items WHERE ingest_id IN (${otherCommitted}) AND ${liveGuard}`).bind(ingestId, ingestId),
    env.DB.prepare(`DELETE FROM raw_projects WHERE ingest_id IN (${otherCommitted}) AND ${liveGuard}`).bind(ingestId, ingestId),
    env.DB.prepare(`DELETE FROM raw_sources WHERE ingest_id IN (${otherCommitted}) AND ${liveGuard}`).bind(ingestId, ingestId),
  ];
}

async function rollbackCommit(env, ingestId, prevLiveId) {
  const stmts = [
    env.DB.prepare("UPDATE ingests SET committed_at = NULL WHERE ingest_id = ? AND (SELECT value FROM settings WHERE key = 'live_ingest_id') = ?").bind(ingestId, ingestId),
    prevLiveId
      ? env.DB.prepare("UPDATE settings SET value = ? WHERE key = 'live_ingest_id' AND value = ?").bind(prevLiveId, ingestId)
      : env.DB.prepare("DELETE FROM settings WHERE key = 'live_ingest_id' AND value = ?").bind(ingestId),
  ];
  await env.DB.batch(stmts);
}

export async function commitIngest(env, ingestId) {
  const row = await getIngest(env, ingestId);
  if (!row) return null;
  const prevLiveId = await getSetting(env, "live_ingest_id");
  if (row.committed_at) {
    if (prevLiveId === ingestId) {
      const counts = await renderUntilPublished(env);
      return { ingest_id: ingestId, ...counts };
    }
    return { error: "already_committed" };
  }
  const at = nowIso();
  const staleBefore = new Date(Date.now() - 60 * 60 * 1000).toISOString().replace(/\.\d{3}Z$/, "Z");
  const results = await env.DB.batch([
    env.DB.prepare(
      `INSERT INTO settings (key, value) VALUES ('live_ingest_id', ?)
       ON CONFLICT(key) DO UPDATE SET value = excluded.value
       WHERE COALESCE(${LIVE_STAMP}settings.value), '')
          <= COALESCE(${LIVE_STAMP}excluded.value), '')`,
    ).bind(ingestId),
    env.DB.prepare(
      `UPDATE ingests SET committed_at = ?
       WHERE ingest_id = ? AND committed_at IS NULL
         AND (SELECT value FROM settings WHERE key = 'live_ingest_id') = ?`,
    ).bind(at, ingestId, ingestId),
  ]);
  if (!results[1]?.meta?.changes) {
    const liveId = await getSetting(env, "live_ingest_id");
    const latest = await getIngest(env, ingestId);
    if (latest?.committed_at && liveId === ingestId) {
      const counts = await renderUntilPublished(env);
      return { ingest_id: ingestId, ...counts };
    }
    return { error: "stale_ingest" };
  }
  await bestEffort(env, "last_ingest_at_failed", () => setSetting(env, "last_ingest_at", at));
  // stale *uncommitted* ingests only: the previous live generation stays until publication succeeds.
  await bestEffort(env, "stale_uncommitted_cleanup_failed", () =>
    env.DB.batch(staleUncommittedCleanup(env, staleBefore, ingestId)),
  );

  let counts;
  try {
    counts = await renderUntilPublished(env);
  } catch (err) {
    await bestEffort(env, "commit_rollback_failed", () => rollbackCommit(env, ingestId, prevLiveId));
    throw err;
  }
  if (counts.published === false) {
    await bestEffort(env, "commit_rollback_failed", () => rollbackCommit(env, ingestId, prevLiveId));
    return { ingest_id: ingestId, ...counts };
  }
  await bestEffort(env, "commit_cleanup_failed", () => env.DB.batch(previousGenerationCleanup(env, ingestId)));
  return { ingest_id: ingestId, ...counts };
}

