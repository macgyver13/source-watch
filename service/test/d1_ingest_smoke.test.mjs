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
  return { status: res.status, text, json };
}

test("D1 smoke: migrate-applied worker boots, ingest+render, /feed.json", async () => {
  // Migrations must already be applied (CI/script runs wrangler d1 migrations apply --local).
  await withWorker(async (worker) => {
    let r = await req(worker, "/feed.json");
    assert.equal(r.status, 200);
    assert.ok(Array.isArray(r.json?.items));

    r = await req(worker, "/api/ingest/begin", { method: "POST", body: {} });
    assert.equal(r.status, 401);

    r = await req(worker, "/api/ingest/begin", {
      method: "POST",
      token: INGEST,
      body: {
        generated_at: "2026-09-11T12:00:00Z",
        watch: {
          name: "Smoke Watch",
          preferred_chips: ["docs"],
          base_url: "http://127.0.0.1/",
        },
        feed_title: "Smoke Watch",
        feed_description: "d1 smoke",
      },
    });
    assert.equal(r.status, 200);
    const ingestId = r.json?.ingest_id;
    assert.ok(ingestId);

    const item = {
      id: "seed:smoke-docs",
      title: "Smoke docs",
      summary: "hello d1",
      source_url: "https://example.com/smoke",
      source_type: "docs_page",
      project: "Smoke",
      tags: ["docs"],
      status: "seeded",
      discovered_at: "2026-09-01T00:00:00Z",
      event_time: "2026-09-01T00:00:00Z",
      activity_at: "2026-09-01T00:00:00Z",
    };
    const project = {
      id: "smoke",
      name: "Smoke",
      sources: ["smoke-docs"],
      discovered_at: "2026-09-01T00:00:00Z",
      activity_at: "2026-09-01T00:00:00Z",
      latest_discovered_at: "2026-09-01T00:00:00Z",
    };
    const source = {
      id: "smoke-docs",
      name: "Smoke docs",
      url: "https://example.com/smoke",
      source_type: "docs_page",
      project: "Smoke",
      confidence: "seeded_source",
    };

    r = await req(worker, "/api/ingest/chunk", {
      method: "POST",
      token: INGEST,
      body: { ingest_id: ingestId, kind: "items", rows: [item] },
    });
    assert.equal(r.status, 200);
    assert.equal(r.json?.written, 1);

    r = await req(worker, "/api/ingest/chunk", {
      method: "POST",
      token: INGEST,
      body: { ingest_id: ingestId, kind: "projects", rows: [project] },
    });
    assert.equal(r.status, 200);

    r = await req(worker, "/api/ingest/chunk", {
      method: "POST",
      token: INGEST,
      body: { ingest_id: ingestId, kind: "sources", rows: [source] },
    });
    assert.equal(r.status, 200);

    r = await req(worker, "/api/ingest/commit", {
      method: "POST",
      token: INGEST,
      body: { ingest_id: ingestId },
    });
    assert.equal(r.status, 200);
    assert.equal(r.json?.published, true);
    assert.equal(r.json?.items, 1);

    r = await req(worker, "/feed.json");
    assert.equal(r.status, 200);
    assert.equal(r.json?.title, "Smoke Watch");
    assert.deepEqual(
      (r.json?.items || []).map((row) => row.id),
      ["seed:smoke-docs"],
    );

    r = await req(worker, "/items.jsonl");
    assert.equal(r.status, 200);
    assert.ok(r.text && r.text.trim().length > 0, "items.jsonl should be present after ingest");
    const jsonlRows = r.text
      .trim()
      .split("\n")
      .filter(Boolean)
      .map((line) => JSON.parse(line));
    assert.deepEqual(
      jsonlRows.map((row) => row.id),
      ["seed:smoke-docs"],
    );

    r = await req(worker, "/api/admin/state");
    assert.equal(r.status, 401);

    r = await req(worker, "/api/admin/state", { token: ADMIN });
    assert.equal(r.status, 200);
    assert.equal(r.json?.raw, 1);
    assert.equal(r.json?.visible, 1);
  });
});
