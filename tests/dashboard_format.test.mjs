import assert from "node:assert/strict";
import test from "node:test";

import { formatDashboardTime } from "../app/dashboard/static/dashboard_format.js";

test("formats an ISO timestamp with date, local time, and timezone", () => {
  const formatted = formatDashboardTime("2026-11-14T15:00:00Z", "en-US");
  assert.match(formatted, /Nov/);
  assert.match(formatted, /2026/);
  assert.match(formatted, /\d{1,2}:\d{2}/);
  assert.doesNotMatch(formatted, /Invalid/);
});

test("keeps missing and invalid timestamps explicit", () => {
  assert.equal(formatDashboardTime(null), "Not available");
  assert.equal(formatDashboardTime("not-a-time"), "not-a-time");
});
