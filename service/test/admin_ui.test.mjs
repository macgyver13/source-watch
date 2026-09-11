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
