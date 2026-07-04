import { HealthSnapshotSchema, readBackendHealth } from "../../../lib/api/health";

export const dynamic = "force-dynamic";

export async function GET() {
  const snapshot = await readBackendHealth();
  const body = HealthSnapshotSchema.parse(snapshot);

  return Response.json(body, {
    headers: {
      "cache-control": "no-store",
    },
  });
}
