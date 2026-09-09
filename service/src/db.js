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

export async function readRendered(env, name) {
  const { results } = await env.DB.prepare("SELECT body FROM rendered WHERE name = ? ORDER BY seq").bind(name).all();
  if (!results || !results.length) return null;
  return results.map((row) => row.body ?? "").join("");
}

export async function readRenderedMeta(env, name) {
  return env.DB.prepare("SELECT etag, content_type, bytes, updated_at FROM rendered_meta WHERE name = ?").bind(name).first();
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
  await writeRendered(env, "feed.json", JSON.stringify(feed), jsonType);
  await writeRendered(env, "projects.json", JSON.stringify(projects), jsonType);
  await writeRendered(env, "sources.json", JSON.stringify(sources), jsonType);
  await writeRendered(env, "watch.json", JSON.stringify(watch), jsonType);
  await writeRendered(env, "items.jsonl", renderJsonl(overlaid.items), "application/jsonl; charset=utf-8");
  await writeRendered(
    env,
    "feed.xml",
    renderRss(overlaid.items, {
      name: feed.title,
      base_url: watch.base_url || "",
      description: feed.description,
    }),
    "application/rss+xml; charset=utf-8",
  );
  await writeRendered(env, "weeks-index", JSON.stringify(weekIndex(overlaid.items)), jsonType);
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
  await env.DB.batch([
    env.DB.prepare("UPDATE ingests SET committed_at = ? WHERE ingest_id = ?").bind(at, ingestId),
    env.DB.prepare("INSERT OR REPLACE INTO settings (key, value) VALUES ('live_ingest_id', ?)").bind(ingestId),
    env.DB.prepare("INSERT OR REPLACE INTO settings (key, value) VALUES ('last_ingest_at', ?)").bind(at),
    env.DB.prepare("DELETE FROM raw_items WHERE ingest_id != ?").bind(ingestId),
    env.DB.prepare("DELETE FROM raw_projects WHERE ingest_id != ?").bind(ingestId),
    env.DB.prepare("DELETE FROM raw_sources WHERE ingest_id != ?").bind(ingestId),
  ]);
  const counts = await renderAll(env);
  return { ingest_id: ingestId, ...counts };
}
