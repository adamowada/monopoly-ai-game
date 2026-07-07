Status: fail

## Lineage Gate

- Selected Design Lineage: `application-coordination`.
- Primary user task: play a local Monopoly-style board game from the board surface, identify the current player, understand cash, ownership, development, token locations, blockers, and legal next actions at a glance, then open secondary research or audit views only when needed.
- Selected Page: `Dashboard Page` only as a route-level application page; the product-specific presentation is a custom game-table page where the board is the dominant object.
- Selected Layout: board-first game-surface layout with the board as the largest region, current-turn command area adjacent to the board, player trays around or near the board, and secondary views in a hamburger drawer plus compact tabs/disclosures.
- Selected Component families: `dashboard-page`, `stacked-lists`, `forms`, `badges`, `grid-layout`, `interaction-patterns`, `responsive-behavior`, `accessibility-patterns`, `icon-systems`, and `tailwind-theme-patterns`.
- Custom tabletop override: replace generic dashboard panels with board-game trays, command panels, deed/card surfaces, token pieces, owner markers, development markers, and a real game drawer.
- Rejected lineage: `application-dashboard`, because it overweights equal panels, summaries, and admin-style monitoring. The target is acting on the current game state from the board, not monitoring a broad dashboard.
- Rejected lineage: `saas-marketing`, because the page is not a persuasive landing page and must not use hero-section rhythm or conversion-page hierarchy.
- Rejected lineage: `ecommerce-product-evaluation`, because property/deed inspection can borrow product-card clarity, but the primary task is not browsing variants, purchasing, or cart flow.
- Rejected lineage: `documentation`, because rules and AI audit views may be dense secondary content, but the normal play page must not read like documentation or logs.
- Color Palette roles: page background is a muted game mat; board surface is warm board paper; board ink is dark green-brown; player colors are reserved for tokens, trays, and owner markers; surface is muted paper/card; elevated surface is warmer deed/card paper; primary text is dark ink; secondary text is muted ink; border is dark board ink for board and softer paper-border for trays; primary accent is current-player color or table accent; secondary accent is deck/card accent; focus is high-contrast teal; success, warning, and danger are semantic only; disabled is visibly muted with useful explanation.
- Density: board has medium game density and highest visual priority; player trays are compact and game-like; current legal actions stay close to the board; supporting records use higher density only after opening drawers, tabs, or disclosures.
- Visual Hierarchy: board first; current player and turn second; legal action command area third; urgent blocker or active payment/debt fourth; player cash, token location, ownership, and development fifth; latest meaningful event sixth; negotiations/contracts/property-management shortcuts seventh; logs, AI audit, raw IDs, and research detail last.
- Affordance rules: inspectable board spaces must be keyboard accessible; legal actions must expose meaningful state-derived descriptions; owner and development markers are visible without hover; hamburger opens and closes a real drawer; primary turn actions remain outside the drawer.
- Hover State rules: hover/focus confirms inspectable spaces, tabs, drawer items, and explicit actions; passive trays and records do not use decorative interactive affordance; disabled actions remain visibly disabled and explain why when it matters.
- Responsive behavior: desktop keeps board, current player, cash, and primary action in the first viewport; tablet keeps board first and command area adjacent or immediately below; mobile keeps board first, command area immediately after, and player trays compressed without hiding primary actions behind the hamburger.
- Accessibility basics: semantic headings and regions, accessible names for board spaces/tokens/decks/actions, visible focus, keyboard drawer/tabs/space inspection, no status by color alone, and reduced-motion-safe dice, token, card, and highlight animations.

## Actual Index Files Read

- `design-lineages.jsonl`
- `patterns.jsonl`
- `examples.jsonl`
- `files.jsonl`
- `relationships.jsonl`

## Source Inspection Boundary

- Web Design Templates guidance inspected:
  - `GLOSSARY.md`
  - `insights/guides/START_HERE.md`
  - `insights/guides/VALIDATION.md`
  - `insights/guides/redesigning-a-website.md`
  - `insights/guides/building-an-admin-app.md`
  - `insights/guides/choosing-a-design-lineage.md`
  - `insights/recipes/admin-dashboard.md`
  - `insights/library/pages/dashboard-page.md`
  - `insights/library/components/stacked-lists.md`
  - `insights/library/components/forms.md`
  - `insights/library/components/badges.md`
  - `insights/library/layouts/grid-layout.md`
  - `insights/library/interactions/interaction-patterns.md`
  - `insights/library/responsive/responsive-behavior.md`
  - `insights/library/accessibility/accessibility-patterns.md`
  - `insights/library/icons/icon-systems.md`
  - `insights/library/styles/tailwind-theme-patterns.md`
  - `skills/web-design-templates/references/smoke-tests.md`
- Selected exemplar records, not raw source templates:
  - `application_blocks/react/page-examples/home-screens/02-stacked.jsx`
  - `application_blocks/react/data-display/description-lists/06-narrow-with-hidden-labels.jsx`
  - `application_blocks/react/elements/badges/18-small-flat-pill-with-dot.jsx`
  - `application_blocks/react/application-shells/multi-column/06-full-width-with-narrow-sidebar-and-header.jsx`
  - `ecommerce_blocks/react/page-examples/product-pages/05-with-tabs-and-related-products.jsx`
  - `marketing/src/components/icons/user-circle-icon.tsx`
  - `online_course/src/app/globals.css`
- Raw Web Design Templates source files were not inspected or copied.
- Current repository source inspection will be recorded during implementation.

## Runtime Verification

- Current status: pending after implementation. This report must remain `Status: fail` until the latest code is built and verified after the ART_PLAN changes.
- Required build command: `pnpm --filter @monopoly-ai-game/web run build`.
- Required unit/component checks: targeted Vitest tests for board art, drawer behavior, player trays, and turn controls, followed by the broader web unit suite when shared components change.
- Required E2E checks: Playwright desktop, tablet, and mobile coverage for board-first layout, hamburger drawer behavior, primary action visibility, and visual overlap/text fit.
- Required screenshot evidence: fresh Playwright screenshots for desktop, tablet, and mobile game routes after the production or verification server is started.
- Required cleanup: stop any web/API servers started during verification and confirm their ports are no longer listening.

## Residual Risks

- Implementation has not yet been completed against the rewritten `ART_PLAN.md`.
- Runtime verification has not yet been rerun after the current art-direction changes.
- The report is intentionally failing until current-state evidence proves the redesigned game surface meets the acceptance checklist.
