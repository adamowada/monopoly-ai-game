const tierStatus = [
  {
    name: "Web tier",
    state: "Runnable",
    detail: "Next.js App Router scaffold with TypeScript checks.",
  },
  {
    name: "API tier",
    state: "Runnable",
    detail: "FastAPI scaffold available as a separate local service.",
  },
  {
    name: "Database tier",
    state: "Pending",
    detail: "Postgres enters the stack in Phase 1 Stage 1.2.",
  },
];

const workAreas = [
  ["Game table", "Unavailable until the rules and frontend stages add playable state."],
  ["Deal console", "Unavailable until negotiation and contract phases define legal actions."],
  ["AI audit", "Unavailable until the Codex AI runtime and memory phases are implemented."],
];

export default function Home() {
  return (
    <main className="consoleShell">
      <section className="masthead" aria-labelledby="console-title">
        <div>
          <p className="stageLabel">Phase 1 Stage 1.1</p>
          <h1 id="console-title">Local Game Research Console</h1>
          <p className="summary">
            Operational scaffold for the local Monopoly-style research game. This surface is a
            working application shell for tier startup checks, not a marketing page.
          </p>
        </div>
        <div className="runState" aria-label="Current scaffold status">
          <span>Stage gate</span>
          <strong>Scaffold online</strong>
        </div>
      </section>

      <section className="statusGrid" aria-label="Tier readiness">
        {tierStatus.map((tier) => (
          <article className="statusPanel" key={tier.name}>
            <div className="panelHeader">
              <h2>{tier.name}</h2>
              <span data-state={tier.state.toLowerCase()}>{tier.state}</span>
            </div>
            <p>{tier.detail}</p>
          </article>
        ))}
      </section>

      <section className="workspaceBand" aria-label="Research workspace readiness">
        <div>
          <h2>Workspace</h2>
          <p>
            The product areas below are visible as planned console regions. Interactive controls are
            withheld until backend rules and action contracts exist.
          </p>
        </div>
        <div className="areaList">
          {workAreas.map(([name, detail]) => (
            <div className="areaRow" key={name}>
              <strong>{name}</strong>
              <span>{detail}</span>
            </div>
          ))}
        </div>
      </section>
    </main>
  );
}
