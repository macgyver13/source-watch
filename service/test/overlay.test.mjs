import assert from "node:assert/strict";
import test from "node:test";
import {
  applyOverlay,
  isoWeekSlug,
  renderJsonl,
  renderRss,
  weekIndex,
} from "../src/overlay.js";

const projectA = {
  id: "alpha",
  name: "Alpha",
  sources: ["a"],
  discovered_at: "2025-12-01T00:00:00Z",
  activity_at: "2025-12-02T00:00:00Z",
  latest_discovered_at: "2025-12-01T00:00:00Z",
};
const projectB = {
  id: "beta",
  name: "Beta",
  sources: ["b"],
  discovered_at: "2025-11-01T00:00:00Z",
  activity_at: "2025-11-02T00:00:00Z",
  latest_discovered_at: "2025-11-01T00:00:00Z",
};
const itemA = {
  id: "seed:a",
  title: "Alpha docs",
  summary: "Quiet summary",
  source_url: "https://example.com/a",
  source_type: "docs_page",
  project: "Alpha",
  tags: ["docs"],
  discovered_at: "2025-12-01T00:00:00Z",
  event_time: "2025-12-01T00:00:00Z",
  activity_at: "2025-12-02T00:00:00Z",
};
const itemB = {
  id: "seed:b",
  title: "Beta docs",
  summary: "Contains Noise in the middle",
  source_url: "https://example.com/b",
  source_type: "docs_page",
  project: "Beta",
  tags: ["docs"],
  discovered_at: "2025-11-01T00:00:00Z",
  event_time: "2025-11-01T00:00:00Z",
  activity_at: "2025-11-02T00:00:00Z",
};
const sourceA = { id: "a", name: "Alpha docs", url: "https://example.com/a", source_type: "docs_page", project: "Alpha" };
const sourceB = { id: "b", name: "Beta docs", url: "https://example.com/b", source_type: "docs_page", project: "Beta" };

test("hiding seed:a drops item, source, and sole project", () => {
  const out = applyOverlay({
    items: [itemA, itemB],
    projects: [projectA, projectB],
    sources: [sourceA, sourceB],
    overrides: { item: { "seed:a": { hidden: true } } },
    exclusions: [],
  });
  assert.deepEqual(out.items.map((i) => i.id), ["seed:b"]);
  assert.deepEqual(out.sources.map((s) => s.id), ["b"]);
  assert.deepEqual(out.projects.map((p) => p.id), ["beta"]);
});

test("term exclusion is case-insensitive on summary", () => {
  const out = applyOverlay({
    items: [itemA, itemB],
    projects: [projectA, projectB],
    sources: [sourceA, sourceB],
    overrides: {},
    exclusions: [{ kind: "term", value: "noise" }],
  });
  assert.deepEqual(out.items.map((i) => i.id), ["seed:a"]);
  assert.equal(out.items[0].summary.includes("Noise"), false);
});

test("discovered_at patch reorders feed and moves ISO week", () => {
  const out = applyOverlay({
    items: [itemA, itemB],
    projects: [projectA, projectB],
    sources: [sourceA, sourceB],
    overrides: { item: { "seed:b": { discovered_at: "2026-01-05T00:00:00Z" } } },
    exclusions: [],
  });
  assert.equal(out.items[0].id, "seed:b");
  assert.equal(out.items[0].discovered_at, "2026-01-05T00:00:00Z");
  assert.equal(out.items[0].event_time, "2026-01-05T00:00:00Z");
  const weeks = weekIndex(out.items);
  assert.equal(weeks.find((w) => w.slug === isoWeekSlug("2026-01-05T00:00:00Z"))?.slug, "2026-W02");
});

test("renderJsonl matches item count and sorts keys", () => {
  const items = [itemA, itemB];
  const body = renderJsonl(items);
  const lines = body.split("\n");
  assert.equal(lines.length, items.length);
  for (const line of lines) {
    const obj = JSON.parse(line);
    assert.deepEqual(Object.keys(obj), Object.keys(obj).sort());
  }
});

test("renderJsonl preserves nested evidence fields", () => {
  const item = {
    ...itemA,
    evidence: [{ url: "https://example.com/a", retrieved_at: "2026-01-01T00:00:00Z", query: "atlas" }],
  };
  const parsed = JSON.parse(renderJsonl([item]));
  assert.equal(parsed.evidence[0].url, "https://example.com/a");
  assert.equal(parsed.evidence[0].query, "atlas");
});


test("renderRss starts with xml declaration and escapes ampersand", () => {
  const xml = renderRss([{ ...itemA, title: "Foo & Bar" }], { name: "Watch", base_url: "https://example.com/", description: "d" });
  assert.ok(xml.startsWith('<?xml version="1.0" encoding="UTF-8"?>'));
  assert.ok(xml.includes("Foo &amp; Bar"));
  assert.equal(xml.includes("Foo & Bar"), false);
});

test("weekIndex uses ISO week-year on 2026-01-01", () => {
  const weeks = weekIndex([{ discovered_at: "2026-01-01T00:00:00Z" }]);
  assert.deepEqual(weeks, [{ slug: "2026-W01", count: 1 }]);
  assert.equal(isoWeekSlug("2026-01-01T00:00:00Z"), "2026-W01");
});

test("hiding a source drops its items from the feed", () => {
  const out = applyOverlay({
    items: [itemA, itemB],
    projects: [projectA, projectB],
    sources: [sourceA, sourceB],
    overrides: { source: { a: { hidden: true } } },
    exclusions: [],
  });
  assert.deepEqual(out.items.map((i) => i.id), ["seed:b"]);
  assert.deepEqual(out.sources.map((s) => s.id), ["b"]);
  assert.deepEqual(out.projects.map((p) => p.id), ["beta"]);
});

test("project title patch renames member items and sources", () => {
  const out = applyOverlay({
    items: [itemA, itemB],
    projects: [projectA, projectB],
    sources: [sourceA, sourceB],
    overrides: { project: { alpha: { title: "Alpha Renamed" } } },
    exclusions: [],
  });
  const alphaItem = out.items.find((i) => i.id === "seed:a");
  const alphaSource = out.sources.find((s) => s.id === "a");
  const alphaProject = out.projects.find((p) => p.id === "alpha");
  assert.equal(alphaItem.project, "Alpha Renamed");
  assert.equal(alphaSource.project, "Alpha Renamed");
  assert.equal(alphaProject.name, "Alpha Renamed");
});

test("project exclusion matches renamed display name", () => {
  const out = applyOverlay({
    items: [itemA, itemB],
    projects: [projectA, projectB],
    sources: [sourceA, sourceB],
    overrides: { project: { alpha: { title: "Alpha Renamed" } } },
    exclusions: [{ kind: "project", value: "Alpha Renamed" }],
  });
  assert.deepEqual(out.items.map((i) => i.id), ["seed:b"]);
  assert.equal(out.projects.length, 1);
  assert.equal(out.projects[0].id, "beta");
});

test("project exclusion still matches original name after rename", () => {
  const out = applyOverlay({
    items: [itemA, itemB],
    projects: [projectA, projectB],
    sources: [sourceA, sourceB],
    overrides: { project: { alpha: { title: "Alpha Renamed" } } },
    exclusions: [{ kind: "project", value: "Alpha" }],
  });
  assert.deepEqual(out.items.map((i) => i.id), ["seed:b"]);
});


test("repo exclusion does not match a longer repository name", () => {
  const keep = {
    ...itemA,
    id: "gh:acme-foobar",
    source_url: "https://github.com/acme/foobar",
    summary: "keep",
  };
  const drop = {
    ...itemB,
    id: "gh:acme-foo",
    source_url: "https://github.com/acme/foo",
    summary: "drop",
  };
  const out = applyOverlay({
    items: [keep, drop],
    projects: [projectA, projectB],
    sources: [
      { ...sourceA, id: "gh:acme-foobar", url: keep.source_url },
      { ...sourceB, id: "gh:acme-foo", url: drop.source_url },
    ],
    overrides: {},
    exclusions: [{ kind: "repo", value: "acme/foo" }],
  });
  assert.deepEqual(out.items.map((i) => i.id), ["gh:acme-foobar"]);
});

