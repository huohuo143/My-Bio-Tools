import assert from "node:assert/strict";
import test from "node:test";
import {
  authorizationIsExpired, AuthorizationPeriodError, resolveAuthorizationPeriod,
} from "../src/authorization.ts";

test("authorization periods use calendar months in Asia/Shanghai", () => {
  const now = Math.floor(Date.parse("2026-01-31T02:15:30Z") / 1000);
  assert.equal(
    resolveAuthorizationPeriod("1_month", undefined, now).expiresAt,
    Math.floor(Date.parse("2026-02-28T02:15:30Z") / 1000),
  );
  assert.equal(
    resolveAuthorizationPeriod("6_months", undefined, now).expiresAt,
    Math.floor(Date.parse("2026-07-31T02:15:30Z") / 1000),
  );
  assert.equal(
    resolveAuthorizationPeriod("1_year", undefined, now).expiresAt,
    Math.floor(Date.parse("2027-01-31T02:15:30Z") / 1000),
  );
  assert.equal(
    resolveAuthorizationPeriod("2_years", undefined, now).expiresAt,
    Math.floor(Date.parse("2028-01-31T02:15:30Z") / 1000),
  );
});

test("permanent and custom authorization periods are explicit", () => {
  const now = Math.floor(Date.parse("2026-07-20T04:00:00Z") / 1000);
  assert.equal(resolveAuthorizationPeriod("permanent", undefined, now).expiresAt, null);
  assert.equal(
    resolveAuthorizationPeriod("custom", "2026-08-05", now).expiresAt,
    Math.floor(Date.parse("2026-08-05T15:59:59Z") / 1000),
  );
  assert.throws(
    () => resolveAuthorizationPeriod("custom", "2026-07-19", now),
    AuthorizationPeriodError,
  );
  assert.throws(
    () => resolveAuthorizationPeriod("unknown", undefined, now),
    AuthorizationPeriodError,
  );
  assert.equal(authorizationIsExpired(now, now), true);
  assert.equal(authorizationIsExpired(null, now), false);
});
