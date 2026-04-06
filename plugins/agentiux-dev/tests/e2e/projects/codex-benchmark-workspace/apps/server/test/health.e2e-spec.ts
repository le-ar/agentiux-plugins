import assert from "node:assert/strict";
import test from "node:test";

import { handleReadinessRequest } from "../src/health/health.controller";

test("GET /ready returns the storefront checkout readiness contract", () => {
  const response = handleReadinessRequest();

  assert.equal(response.method, "GET");
  assert.equal(response.path, "/ready");
  assert.deepEqual(response.body, {
    status: "ok",
    source: "storefront-checkout",
  });
});
