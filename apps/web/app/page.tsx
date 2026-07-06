import { DashboardShell } from "./dashboard-shell";
import { readBackendHealth } from "../lib/api/health";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function Home() {
  const initialHealth = await readBackendHealth();
  const title = "Monopoly 2.0 Game Table";

  return <DashboardShell initialHealth={initialHealth} title={title} />;
}
