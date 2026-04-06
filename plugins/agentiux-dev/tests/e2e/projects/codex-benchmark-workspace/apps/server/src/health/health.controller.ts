import { readinessPayload } from "./health.service";

export function handleReadinessRequest() {
  return {
    method: "GET",
    path: "/ready",
    body: readinessPayload(),
  };
}
