import assert from "node:assert/strict";
import test from "node:test";
import { unstable_dev } from "wrangler";

const INGEST = "devingest";
const ADMIN = "devadmin";

async function withWorker(fn) {
  const worker = await unstable_dev("service/src/index.js", {
    config: "wrangler.smoke.jsonc",
    local: true,
    persist: true,
    ip: "127.0.0.1",
    logLevel: "error",
    experimental: { disableExperimentalWarning: true, forceLocal: true },
  });
  try {
    await fn(worker);
  } finally {
    await worker.stop();
  }
}

async function req(worker, path, { method = "GET", token, body } = {}) {
  const headers = { Accept: "application/json" };
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const res = await worker.fetch(`http://127.0.0.1${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    /* non-json */
  }
  return { status: res.status, text, json, headers: res.headers };
}

function catalog(generatedAt, id, title) {
  const item = {
    id,
    title,
    summary: title,
    source_url: `https://example.com/${id}`,
    source_type: "docs_page",
    project: title,
    tags: ["docs"],
    status: "seeded",
    discovered_at: generatedAt,
    event_time: generatedAt,
    activity_at: generatedAt,
  };
  const project = {
    id: `${id}-project`,
    name: title,
    sources: [`${id}-source`],
    discovered_at: generatedAt,
    activity_at: generatedAt,
    latest_discovered_at: generatedAt,
  };
  const source = {
    id: `${id}-source`,
    name: title,
    url: `https://example.com/${id}`,
    source_type: "docs_page",
    project: title,
    confidence: "seeded_source",
  };
  return { item, project, source };
}

async function beginIngest(worker, generatedAt, title) {
  const r = await req(worker, "/api/ingest/begin", {
    method: "POST",
    token: INGEST,
    body: {
      generated_at: generatedAt,
      watch: { name: title, preferred_chips: ["docs"], base_url: "http://127.0.0.1/" },
      feed_title: title,
      feed_description: title,
    },
  });
  assert.equal(r.status, 200, r.text);
  assert.ok(r.json?.ingest_id);
  return r.json.ingest_id;
}

async function chunkCatalog(worker, ingestId, rows) {
  for (const [kind, row] of [
    ["items", rows.item],
    ["projects", rows.project],
    ["sources", rows.source],
  ]) {
    const r = await req(worker, "/api/ingest/chunk", {
      method: "POST",
      token: INGEST,
      body: { ingest_id: ingestId, kind, rows: [row] },
    });
    assert.equal(r.status, 200, r.text);
  }
}

async function commit(worker, ingestId) {
  return req(worker, "/api/ingest/commit", {
    method: "POST",
    token: INGEST,
    body: { ingest_id: ingestId },
  });
}

test("D1 ingest integrity: closed chunks, stale commit, replace generation", async () => {
  await withWorker(async (worker) => {
    const liveAt = "2026-09-11T12:00:00Z";
    const newer = catalog(liveAt, "seed:integrity-new", "Integrity New");
    const ingestA = await beginIngest(worker, liveAt, "Integrity New");
    await chunkCatalog(worker, ingestA, newer);
    let r = await commit(worker, ingestA);
    assert.equal(r.status, 200, r.text);
    assert.equal(r.json?.published, true);

    r = await req(worker, "/api/admin/state", { token: ADMIN });
    assert.equal(r.status, 200, r.text);
    const rawAfterCommit = r.json?.raw;
    assert.equal(rawAfterCommit, 1);

    r = await req(worker, "/api/ingest/chunk", {
      method: "POST",
      token: INGEST,
      body: { ingest_id: ingestA, kind: "items", rows: [newer.item] },
    });
    assert.equal(r.status, 409, r.text);
    assert.equal(r.json?.error, "already_committed");

    r = await req(worker, "/api/admin/state", { token: ADMIN });
    assert.equal(r.status, 200, r.text);
    assert.equal(r.json?.raw, rawAfterCommit);

    const olderAt = "2026-09-10T12:00:00Z";
    const older = catalog(olderAt, "seed:integrity-old", "Integrity Old");
    const ingestB = await beginIngest(worker, olderAt, "Integrity Old");
    await chunkCatalog(worker, ingestB, older);
    r = await commit(worker, ingestB);
    assert.equal(r.status, 409, r.text);
    assert.equal(r.json?.error, "stale_ingest");

    r = await req(worker, "/feed.json");
    assert.equal(r.status, 200, r.text);
    assert.deepEqual(
      (r.json?.items || []).map((row) => row.id),
      ["seed:integrity-new"],
    );
    const replaceAt = "2026-09-11T12:00:00Z";
    const first = catalog(replaceAt, "seed:integrity-a", "Integrity A");
    const second = catalog(replaceAt, "seed:integrity-b", "Integrity B");
    const ingestC = await beginIngest(worker, replaceAt, "Integrity Replace");
    r = await req(worker, "/api/ingest/chunk", {
      method: "POST",
      token: INGEST,
      body: { ingest_id: ingestC, kind: "items", rows: [first.item, second.item] },
    });
    assert.equal(r.status, 200, r.text);
    r = await req(worker, "/api/ingest/chunk", {
      method: "POST",
      token: INGEST,
      body: { ingest_id: ingestC, kind: "projects", rows: [first.project, second.project] },
    });
    assert.equal(r.status, 200, r.text);
    r = await req(worker, "/api/ingest/chunk", {
      method: "POST",
      token: INGEST,
      body: { ingest_id: ingestC, kind: "sources", rows: [first.source, second.source] },
    });
    assert.equal(r.status, 200, r.text);
    r = await commit(worker, ingestC);
    assert.equal(r.status, 200, r.text);
    assert.equal(r.json?.published, true);
    assert.equal(r.json?.items, 2);

    r = await req(worker, "/feed.json");
    assert.equal(r.status, 200, r.text);
    assert.deepEqual(
      (r.json?.items || []).map((row) => row.id).sort(),
      ["seed:integrity-a", "seed:integrity-b"],
    );

    r = await req(worker, "/api/admin/state", { token: ADMIN });
    assert.equal(r.status, 200, r.text);
    assert.equal(r.json?.raw, 2);
  });
});
