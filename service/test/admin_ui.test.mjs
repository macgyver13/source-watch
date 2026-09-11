import assert from "node:assert/strict";
import test from "node:test";
import { ADMIN_HTML } from "../src/admin-ui.js";

function loadEditHelpers() {
  const start = ADMIN_HTML.indexOf("function pad2");
  const end = ADMIN_HTML.indexOf("function repoFromUrl");
  if (start < 0 || end <= start) {
    throw new Error("admin datetime helpers not found");
  }
  return new Function(
    `${ADMIN_HTML.slice(start, end)}; return { pad2, toLocal, fromLocal, collectEditPatch };`,
  )();
}

test("fromLocal parses datetime-local including optional seconds", () => {
  const { fromLocal } = loadEditHelpers();
  const withSeconds = fromLocal("2026-09-01T12:30:05");
  const withoutSeconds = fromLocal("2026-09-01T12:30");
  const zeroSeconds = fromLocal("2026-09-01T12:30:00");
  assert.match(withSeconds, /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$/);
  assert.equal(withoutSeconds, zeroSeconds);
  assert.notEqual(withSeconds, zeroSeconds);
  assert.equal(fromLocal(""), null);
});

test("title-only save ignores datetime-local zero-second normalization", () => {
  const { collectEditPatch } = loadEditHelpers();
  const patch = collectEditPatch([
    { key: "title", orig: "Old title", val: "New title" },
    { key: "summary", orig: "Same", val: "Same" },
    { key: "discovered_at", orig: "2026-09-01T12:30:00", val: "2026-09-01T12:30" },
    { key: "activity_at", orig: "2026-09-02T08:00:00", val: "2026-09-02T08:00" },
  ]);
  assert.deepEqual(patch, { title: "New title" });
});

test("datetime-local second edits still persist", () => {
  const { collectEditPatch, fromLocal } = loadEditHelpers();
  const patch = collectEditPatch([
    { key: "activity_at", orig: "2026-09-01T12:30:00", val: "2026-09-01T12:30:05" },
  ]);
  assert.deepEqual(patch, { activity_at: fromLocal("2026-09-01T12:30:05") });
});

test("unchanged datetime-local with seconds omitted is not a patch", () => {
  const { collectEditPatch } = loadEditHelpers();
  const patch = collectEditPatch([
    { key: "discovered_at", orig: "2026-09-01T12:30:00", val: "2026-09-01T12:30:00" },
    { key: "activity_at", orig: "2026-09-01T12:30", val: "2026-09-01T12:30:00" },
  ]);
  assert.deepEqual(patch, {});
});

test("fromLocal fills omitted seconds and ADMIN_HTML keeps digit classes", () => {
  const { fromLocal } = loadEditHelpers();
  assert.match(fromLocal("2026-09-01T12:30"), /^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:00Z$/);
  assert.ok(!/\(d\{[0-9]/.test(ADMIN_HTML));
});

test("admin console surfaces failed mutations and scopes the toolbar", () => {
  assert.match(ADMIN_HTML, /id="op-err"/);
  assert.match(ADMIN_HTML, /if \(!res\.ok\)/);
  assert.match(ADMIN_HTML, /catalogOffset >= total/);
  assert.match(ADMIN_HTML, /function pageQs\(\)/);
  assert.match(ADMIN_HTML, /function updateToolbar\(\)/);
  assert.match(ADMIN_HTML, /tab === "rules" \|\| tab === "audit"/);
  assert.doesNotMatch(ADMIN_HTML, /_status >= 400/);
});

test("admin console surfaces read failures and replaces stale refresh feedback", () => {
  assert.match(ADMIN_HTML, /\$\("login-err"\)\.textContent = ""/);
  assert.match(ADMIN_HTML, /if \(request\) return request\.catch\(showError\)/);
  assert.match(ADMIN_HTML, /Promise\.all\(\[loadExclusions\(\), loadTerms\(\), loadSeeds\(\), loadSettings\(\)\]\)\.catch\(showError\)/);
  assert.match(ADMIN_HTML, /api\("\/api\/admin\/state"\)\.then\(showApp\)\.catch\(showError\)/);
  assert.doesNotMatch(ADMIN_HTML, /catch\(function \(\) \{\}\)/);
  assert.match(ADMIN_HTML, /note\.textContent = ""/);
  assert.match(ADMIN_HTML, /data\.body \? ": " \+ String\(data\.body\)\.slice\(0, 180\)/);
});
