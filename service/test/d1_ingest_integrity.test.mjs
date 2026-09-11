import assert from "node:assert/strict";
import test from "node:test";
import { unstable_dev } from "wrangler";
import { ROW_BATCH } from "../src/db.js";

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

test("D1 ingest integrity: concurrent commits of one ingest stay live", async () => {
  await withWorker(async (worker) => {
    const at = "2026-09-11T12:00:00Z";
    const rows = catalog(at, "seed:integrity-cas", "Integrity CAS");
    const ingestId = await beginIngest(worker, at, "Integrity CAS");
    await chunkCatalog(worker, ingestId, rows);
    const [a, b] = await Promise.all([commit(worker, ingestId), commit(worker, ingestId)]);
    const statuses = [a.status, b.status].sort();
    assert.ok(statuses.every((status) => status === 200), `${a.status} ${a.text} / ${b.status} ${b.text}`);
    assert.equal(a.json?.published, true);
    assert.equal(b.json?.published, true);

    let r = await req(worker, "/feed.json");
    assert.equal(r.status, 200, r.text);
    assert.deepEqual(
      (r.json?.items || []).map((row) => row.id),
      ["seed:integrity-cas"],
    );

    r = await req(worker, "/api/admin/state", { token: ADMIN });
    assert.equal(r.status, 200, r.text);
    assert.equal(r.json?.raw, 1);

    r = await commit(worker, ingestId);
    assert.equal(r.status, 200, r.text);
    assert.equal(r.json?.published, true);

    r = await req(worker, "/feed.json");
    assert.deepEqual(
      (r.json?.items || []).map((row) => row.id),
      ["seed:integrity-cas"],
    );
  });
});

test("D1 ingest integrity: an older equal-stamp ingest cannot replace a newer one", async () => {
  await withWorker(async (worker) => {
    const at = "2026-09-11T12:00:00Z";
    const olderRows = catalog(at, "seed:equal-stamp-old", "Equal Stamp Old");
    const newerRows = catalog(at, "seed:equal-stamp-new", "Equal Stamp New");
    const olderId = await beginIngest(worker, at, "Equal Stamp Old");
    await chunkCatalog(worker, olderId, olderRows);
    const newerId = await beginIngest(worker, at, "Equal Stamp New");
    await chunkCatalog(worker, newerId, newerRows);

    let r = await commit(worker, newerId);
    assert.equal(r.status, 200, r.text);
    assert.equal(r.json?.published, true);

    r = await commit(worker, olderId);
    assert.equal(r.status, 409, r.text);
    assert.equal(r.json?.error, "stale_ingest");

    r = await req(worker, "/feed.json");
    assert.equal(r.status, 200, r.text);
    assert.deepEqual(
      (r.json?.items || []).map((row) => row.id),
      ["seed:equal-stamp-new"],
    );
  });
});

test("D1 ingest integrity: multi-slice chunk is all-or-nothing with commit", async () => {
  await withWorker(async (worker) => {
    const at = "2026-09-11T12:00:00Z";
    const count = ROW_BATCH + 5;
    const items = [];
    const projects = [];
    const sources = [];
    for (let i = 0; i < count; i++) {
      const rows = catalog(at, `seed:integrity-batch-${i}`, `Batch ${i}`);
      items.push(rows.item);
      projects.push(rows.project);
      sources.push(rows.source);
    }
    const ingestId = await beginIngest(worker, at, "Integrity Batch");
    let r = await req(worker, "/api/ingest/chunk", {
      method: "POST",
      token: INGEST,
      body: { ingest_id: ingestId, kind: "items", rows: items },
    });
    assert.equal(r.status, 200, r.text);
    assert.ok(r.json?.written >= count, r.text);
    r = await req(worker, "/api/ingest/chunk", {
      method: "POST",
      token: INGEST,
      body: { ingest_id: ingestId, kind: "projects", rows: projects },
    });
    assert.equal(r.status, 200, r.text);
    r = await req(worker, "/api/ingest/chunk", {
      method: "POST",
      token: INGEST,
      body: { ingest_id: ingestId, kind: "sources", rows: sources },
    });
    assert.equal(r.status, 200, r.text);
    r = await commit(worker, ingestId);
    assert.equal(r.status, 200, r.text);
    assert.equal(r.json?.items, count);

    r = await req(worker, "/feed.json");
    assert.equal(r.status, 200, r.text);
    assert.equal((r.json?.items || []).length, count);

    r = await req(worker, "/api/ingest/chunk", {
      method: "POST",
      token: INGEST,
      body: { ingest_id: ingestId, kind: "items", rows: items },
    });
    assert.equal(r.status, 409, r.text);
    assert.equal(r.json?.error, "already_committed");

    r = await req(worker, "/feed.json");
    assert.equal((r.json?.items || []).length, count);
  });
});

test("D1 admin: case-equivalent exclusion is 409 and reversible", async () => {
  await withWorker(async (worker) => {
    const at = "2026-09-11T12:00:00Z";
    const rows = catalog(at, "seed:noise-doc", "Quiet Doc");
    rows.item.summary = "contains Noise in the summary";
    const ingestId = await beginIngest(worker, at, "Noise Watch");
    await chunkCatalog(worker, ingestId, rows);
    let r = await commit(worker, ingestId);
    assert.equal(r.status, 200, r.text);

    r = await req(worker, "/feed.json");
    assert.ok((r.json?.items || []).some((row) => row.id === "seed:noise-doc"));

    r = await req(worker, "/api/admin/exclusions", {
      method: "POST",
      token: ADMIN,
      body: { kind: "term", value: "Noise" },
    });
    assert.equal(r.status, 200, r.text);

    r = await req(worker, "/api/admin/exclusions", {
      method: "POST",
      token: ADMIN,
      body: { kind: "term", value: "noise" },
    });
    assert.equal(r.status, 409, r.text);
    assert.equal(r.json?.error, "duplicate_exclusion");

    r = await req(worker, "/api/admin/exclusions", { token: ADMIN });
    const matches = (r.json?.exclusions || []).filter(
      (row) => row.kind === "term" && String(row.value).toLowerCase() === "noise",
    );
    assert.equal(matches.length, 1);

    r = await req(worker, "/feed.json");
    assert.ok(!(r.json?.items || []).some((row) => row.id === "seed:noise-doc"));

    r = await req(worker, `/api/admin/exclusions/${matches[0].id}`, { method: "DELETE", token: ADMIN });
    assert.equal(r.status, 200, r.text);

    r = await req(worker, "/feed.json");
    assert.ok((r.json?.items || []).some((row) => row.id === "seed:noise-doc"));
  });
});

test("D1 admin: seed locator uniqueness is atomic", async () => {
  await withWorker(async (worker) => {
    const first = {
      kind: "github_repositories",
      entry: { id: "seed-one", repo: "acme/integrity-widget" },
    };
    const second = {
      kind: "github_repositories",
      entry: { id: "seed-two", repo: "acme/integrity-widget" },
    };
    let r = await req(worker, "/api/admin/seed-additions", {
      method: "POST",
      token: ADMIN,
      body: first,
    });
    assert.equal(r.status, 200, r.text);

    r = await req(worker, "/api/admin/seed-additions", {
      method: "POST",
      token: ADMIN,
      body: second,
    });
    assert.equal(r.status, 409, r.text);
    assert.equal(r.json?.error, "duplicate_seed_locator");

    r = await req(worker, "/api/admin/seed-additions", { token: ADMIN });
    const rows = (r.json?.seed_additions || []).filter(
      (row) => String(row.entry?.repo || "").toLowerCase() === "acme/integrity-widget",
    );
    assert.equal(rows.length, 1);

    r = await req(worker, `/api/admin/seed-additions/${rows[0].id}`, { method: "DELETE", token: ADMIN });
    assert.equal(r.status, 200, r.text);

    r = await req(worker, "/api/admin/seed-additions", {
      method: "POST",
      token: ADMIN,
      body: second,
    });
    assert.equal(r.status, 200, r.text);
  });
});

test("D1 admin: concurrent override writes do not merge", async () => {
  await withWorker(async (worker) => {
    const at = "2026-09-11T12:00:00Z";
    const rows = catalog(at, "seed:cas-item", "CAS Item");
    const ingestId = await beginIngest(worker, at, "CAS Watch");
    await chunkCatalog(worker, ingestId, rows);
    let r = await commit(worker, ingestId);
    assert.equal(r.status, 200, r.text);

    const [a, b] = await Promise.all([
      req(worker, "/api/admin/overrides/item/seed:cas-item", {
        method: "PUT",
        token: ADMIN,
        body: { title: "CAS Alpha" },
      }),
      req(worker, "/api/admin/overrides/item/seed:cas-item", {
        method: "PUT",
        token: ADMIN,
        body: { title: "CAS Beta" },
      }),
    ]);
    assert.ok([a.status, b.status].includes(200), `${a.status} ${a.text} / ${b.status} ${b.text}`);
    for (const row of [a, b]) {
      assert.ok(row.status === 200 || (row.status === 409 && row.json?.error === "override_conflict"), row.text);
    }

    r = await req(worker, "/api/admin/items?visibility=all", { token: ADMIN });
    const item = (r.json?.items || []).find((row) => row.id === "seed:cas-item");
    assert.ok(item, "cas item missing from admin list");
    const title = item.patch?.title;
    assert.ok(title === "CAS Alpha" || title === "CAS Beta", JSON.stringify(item.patch));
  });
});
