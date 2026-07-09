import { DashboardShell } from "./dashboard-shell";

export const dynamic = "force-dynamic";
export const revalidate = 0;

export default async function Home() {
  const title = "Monopoly 2.0 Game Table";

  return <DashboardShell title={title} />;
}
