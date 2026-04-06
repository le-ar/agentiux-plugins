import "reflect-metadata";

import { adminMetadata } from "./admin/admin.controller";
import { handleReadinessRequest } from "./health/health.controller";

export async function bootstrap() {
  return {
    adminMetadata,
    readiness: handleReadinessRequest(),
  };
}
