import { DashboardShell } from "./dashboard-shell";
import { readBackendHealth } from "../lib/api/health";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function Home() {
  const initialHealth = await readBackendHealth();
  const title = "Local Game Research Console";

  return <DashboardShell initialHealth={initialHealth} title={title} />;
}
