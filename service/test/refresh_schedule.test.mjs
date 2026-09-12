import assert from "node:assert/strict";
import test from "node:test";
import { cronWindowAllows, scheduledRefreshAllowed } from "../src/index.js";

const daily = "17 2 * * *";

test("daily cron allows 02:17 UTC", () => {
  assert.equal(cronWindowAllows(daily, "2026-09-12T02:17:00Z"), true);
});

test("daily cron rejects other hours at minute 17", () => {
  assert.equal(cronWindowAllows(daily, "2026-09-12T14:17:00Z"), false);
  assert.equal(cronWindowAllows(daily, "2026-09-12T01:17:00Z"), false);
});

test("hourly expected cron allows every hour at minute 17", () => {
  assert.equal(cronWindowAllows("17 * * * *", "2026-09-12T14:17:00Z"), true);
});

test("unset REFRESH_CRON allows any scheduled fire", () => {
  const gate = scheduledRefreshAllowed("", {
    cron: "17 * * * *",
    scheduledTime: Date.parse("2026-09-12T14:17:00Z"),
  });
  assert.equal(gate.ok, true);
});

test("ghost hourly expression is rejected when daily is expected", () => {
  const gate = scheduledRefreshAllowed(daily, {
    cron: "17 * * * *",
    scheduledTime: Date.parse("2026-09-12T14:17:00Z"),
  });
  assert.equal(gate.ok, false);
  assert.equal(gate.reason, "unexpected_cron");
});

test("same daily expression firing off-hour is rejected", () => {
  const gate = scheduledRefreshAllowed(daily, {
    cron: daily,
    scheduledTime: Date.parse("2026-09-12T14:17:00Z"),
  });
  assert.equal(gate.ok, false);
  assert.equal(gate.reason, "outside_cron_window");
});

test("daily expression at 02:17 UTC is allowed", () => {
  const gate = scheduledRefreshAllowed(daily, {
    cron: daily,
    scheduledTime: Date.parse("2026-09-12T02:17:08Z"),
  });
  assert.equal(gate.ok, true);
});
