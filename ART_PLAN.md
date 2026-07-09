# ART_PLAN.md

## Authority

`ART_PLAN.md` is the authoritative art-direction and frontend presentation plan for this
repository. It governs the visual identity, board presentation, player information architecture,
game-surface layout, game art, and art-related verification work.

When `ART_PLAN.md` conflicts with older visual-asset constraints in `PLANS.md`, follow
`ART_PLAN.md` for art direction and asset format decisions while preserving all local-only,
original-art, test-driven-development, and no-IP-copying requirements from `PLANS.md`.

This document is written for an autonomous Codex Goal process. A future Codex process should be
able to read this file, inspect the existing implementation, write tests first, implement the art
direction in coherent increments, verify the result, and stop for review at appropriate boundaries
without asking for clarification.

## Core Objective

The finished application must look and feel like a playable digital board game, not a research
dashboard with a board component inside it.

The target presentation is a polished, 2D, top-down tabletop game surface:

- The board is the dominant object.
- Player state is arranged around the board as game trays, pieces, tabs, and compact cards.
- Current turn and legal actions are obvious at a glance.
- Ownership, development, money, token location, and urgent blockers are visible without scanning
  an admin-style page.
- AI auditability and research inspection remain available, but they do not dominate normal play.

The project should emulate the clarity of a top-down Monopoly-style video game interface without
copying Monopoly IP, official trade dress, official board scans, mascot art, card art, typography,
logos, wording, or 3D production scope.

## Design Lineage Decision

Use `application-coordination` as the functional Design Lineage, with a required custom tabletop
game-surface override.

Primary user task:

- Play a hotseat or AI-assisted turn from the board.
- See whose turn it is.
- See the legal next actions.
- Understand ownership, money, token positions, development, payments, negotiations, and blockers.
- Inspect deeper records only when needed.

Selected page type:

- A game-table application page, implemented as a specialized `Dashboard Page` only in the sense
  that it coordinates game records and actions.

Selected layout:

- Board-first `Layout`.
- Current-turn command area next to or below the board.
- Player trays around the board.
- Secondary drawers, tabs, or trays for supporting records.
- Hamburger game drawer for wayfinding, session utilities, and research/debug views.

Selected component families:

- Board surface.
- Player tray.
- Current-turn command area.
- Property/deed mini-card.
- Owner marker.
- Development marker.
- Token piece.
- Deck and drawn-card presentation.
- Game drawer.
- Compact tabs for secondary views.
- Alerts only for current blockers.

Rejected lineages:

- `application-dashboard`: too metrics/admin-oriented. It encourages equal-weight panels, tables,
  and status summaries that caused the current dashboard feel.
- `saas-marketing`: wrong primary task. The product is not explaining value or converting users.
- `ecommerce-product-evaluation`: useful only as a distant analogy for card/product inspection,
  but purchase-flow hierarchy does not fit turn-based play.
- `documentation`: audit views may borrow documentation density, but the main play page must not
  read like documentation or logs.

Color Palette role assignment:

- Page background: quiet game mat color, not wood grain and not plain admin gray.
- Board surface: warm board-paper green or other original local board color.
- Board ink: dark brown/green-black for outlines and high-contrast board text.
- Property group colors: classic group colors may remain for compatibility.
- Player colors: user-selected colors, reserved for tokens, player trays, and owner markers.
- Surface: muted paper/card surface for trays and cards.
- Elevated surface: slightly warmer card/deed paper.
- Primary text: dark board ink.
- Secondary text: muted ink.
- Border: dark board ink for board, softer paper-border color for trays.
- Primary accent: active current-player color or a consistent table accent.
- Secondary accent: deck/card accent.
- Success, warning, danger: semantic only, never decorative filler.
- Focus state: high-contrast teal or equivalent accessible focus ring.
- Disabled: visibly muted, with explanation when the disabled state matters.

Density rules:

- The board has high visual priority and medium information density.
- Player trays use compact, game-like density.
- Legal action controls stay close to the board and current player.
- Supporting records use higher density only after being opened in drawers, tabs, or secondary
  sections.
- The main play viewport must not show every research/audit panel by default.

Visual Hierarchy rules:

1. Board.
2. Current player and current turn.
3. Legal action command area.
4. Urgent blocker or active payment/debt.
5. Player money, token position, ownership, development.
6. Latest meaningful event.
7. Negotiation/contract/property management shortcuts.
8. Logs, AI audit, raw IDs, state hashes, and research details.

Affordance and interaction rules:

- Board spaces with details must look inspectable and must be keyboard accessible.
- Owner markers and development markers must be visible without requiring hover.
- Legal actions must expose useful descriptions, not only generic button labels.
- A hamburger button must open and close a real drawer or disclosure; it must not be a decorative
  or nearly empty menu.
- Primary turn actions must not be hidden behind the hamburger.
- Secondary inspection views may live behind the hamburger, tabs, disclosures, or drawers.

Accessibility basics:

- Meaningful visuals require accessible names or equivalent adjacent visible text.
- Decorative visual texture must be hidden from assistive technology.
- Color cannot be the only ownership, status, development, or active-player indicator.
- Keyboard users must be able to inspect board spaces, activate legal actions, open/close the
  game drawer, navigate tabs, and dismiss modal card reveals.
- Reduced-motion preferences must dampen dice, token, card, and highlight animations.

## Reference Image Interpretation

A top-down Monopoly video game reference was reviewed. The goal is not to copy its IP, art, or 3D
production choices. The goal is to emulate the useful information architecture.

What to emulate:

- Clear player trays around the board.
- Strong current-player emphasis.
- Immediate visibility of player cash.
- Immediate visibility of each player's token location.
- Compact visual ownership summaries.
- Development markers visible on the property spaces themselves.
- Organized cards, tabs, icons, and game pieces around the board.
- Board-adjacent information rather than a long page of admin panels.

What not to emulate:

- Busy board center.
- Wooden table background.
- Official Monopoly trade dress or IP.
- Full 3D board/city modeling.
- Individually modeled money stacks or detailed property stacks that do not justify the development
  budget.
- Recoloring whole property tiles to indicate ownership when that duplicates player markers.

Neutral or low-priority ideas:

- 3D effects may be used only as subtle depth, shadow, or token polish.
- Rich bitmap art is allowed, but small board squares may use simpler SVG/CSS motifs when clearer.
- Physical card and token metaphors matter more than literal 3D models.

## IP And Originality Rules

All visuals must be original and local to this repository.

Do not copy:

- Official Monopoly board scans.
- Official Monopoly logos, wordmarks, plaques, mascot art, token art, card art, or typography.
- Official card illustrations or official card/rule wording.
- Screenshots or downloaded protected artwork.
- Third-party logos or recognizable brand marks.
- The exact reference-image center composition, UI layout, or art assets.

The temporary working motif remains `Monopoly 2.0` until a more original game title is chosen.
Use it as plain text only. Do not imitate the official Monopoly wordmark, red plaque, mascot,
card styling, or board-center trade dress.

Original art may be:

- Code-native SVG/React drawing.
- CSS-drawn motifs.
- Local SVG assets.
- AI-generated original bitmap art stored in the repository.
- Hybrid generated art that is reviewed and simplified into SVG or React components.

## Page And Layout Requirements

### Main Game Page

The active game route must read as a game table from the first viewport.

Required:

- The board is the largest and most visually important element.
- Current player and primary legal actions are immediately adjacent to the board.
- Player trays surround, flank, or sit near the board according to viewport size.
- Secondary systems are reachable but visually demoted.
- The page must not begin with or default to a dashboard, table-check, health-check, or audit-first
  presentation.

Desktop layout:

- Prefer board centered or slightly left of center.
- Place a current-turn command area beside the board.
- Place player trays along board sides, top/bottom bands, or a compact rail.
- Keep board, active player, cash, and legal next action visible without scrolling at common desktop
  sizes.

Tablet layout:

- Board remains first.
- Current-turn command area stays immediately below or beside board.
- Player trays may become a horizontal strip or two-column tray layout.

Mobile layout:

- Board remains first and playable.
- Current-turn command area follows immediately after board.
- Player trays compress into scrollable chips or compact expandable trays.
- Hamburger drawer handles wayfinding and secondary views.
- Do not hide primary legal actions inside the hamburger.

### Setup/Home Page

The setup page should feel like preparing a local board game table, not configuring an admin system.

Required direction:

- Remove or demote operational shell language such as table checks, rulings, and table areas from
  the first impression unless needed for an error state.
- Present player setup as seat/token selection rather than a dense admin table when feasible.
- Keep backend health visible only as a small readiness indicator or troubleshooting disclosure.
- Use game-facing text and visual treatment.

## Board Requirements

The board is the hero object of the play page.

Required:

- Render all 40 spaces.
- Render visible visual motifs for non-street spaces.
- Keep street-property rectangles text-first: group band, name, price, tokens, owner markers, and
  development markers, without per-square motif art.
- Keep property group color bands recognizable.
- Keep classic space names and prices for the current compatibility theme.
- Show token positions directly on spaces.
- Show ownership directly on or near spaces.
- Show development directly on properties.
- Keep center content simple and game-facing.

Forbidden on the board surface:

- Backend state explanations.
- Stable index notes.
- Vector-only disclaimers.
- State hashes.
- Event IDs.
- Raw API labels.
- Audit/debug copy.
- Long instructional paragraphs.

### Board Center

The center should be calmer than the reference image.

Allowed center content:

- Custom title mark.
- Chance deck.
- Community Chest deck.
- Dice or dice result when relevant.
- Latest drawn card reveal while active.
- Winner celebration when the game is over.

Do not fill the center with dense city scenery, dashboard text, logs, status tables, or permanent
rules copy.

### Board Spaces

Every board space must have:

- Visible name.
- Price or amount where relevant.
- Accessible name.
- Inspectable detail state where useful.

Street properties:

- Keep property group band as the primary color cue.
- Do not render per-square motif art inside the street rectangle.
- Preserve room for name, price, token, owner marker, and development marker.
- Do not recolor the full tile to match the owner. Use owner markers instead.

Railroads and utilities:

- Use distinctive symbols that read at board scale.
- Preserve price visibility.

Tax spaces:

- Show the tax amount clearly.
- Use a simple tax/ledger/luxury motif.

Chance and Community Chest:

- Use deck-specific motifs.
- Show card/deck identity clearly.

Corners:

- Use larger corner compositions because they have more room.
- Keep corner labels clear at board scale.

## Ownership, Development, And Token Language

Ownership must be clear at a glance without recoloring whole property tiles.

Preferred ownership treatments:

- Small owner tabs on the outside edge of the board.
- Player-color chips attached to property tiles.
- Thin owner ribbons or corner markers.
- Mini token badge over owned property.

Avoid:

- Replacing property group color with owner color.
- Large translucent overlays that make names and prices harder to read.
- Repeating the same ownership fact in multiple nearby pills.

Development must be visible on the property itself.

Preferred development treatments:

- House pips on the property band.
- Small house/hotel icons.
- Stacked short bars.
- One clear hotel marker when converted.

Development markers must:

- Be visible at board scale.
- Not obscure property name or price.
- Be distinguishable from ownership.
- Have accessible text through the space detail.

Tokens must feel like game pieces, not dashboard status dots.

Preferred token treatments:

- Distinct flat silhouettes for seats.
- Local SVG token set or React-drawn token shapes.
- Player color fill plus high-contrast outline.
- Small label or tooltip for player name.
- Movement and landing animation with reduced-motion support.

Avoid:

- Plain circular initials as the final token design.
- Tokens that are too similar across players.
- Tiny markers that disappear when multiple players stack.

## Player Tray Requirements

Player trays are the main replacement for dashboard status panels.

Each player tray should show:

- Player name.
- Controller type only when useful, such as human or AI.
- Token piece.
- Cash total.
- Current board location or token marker.
- Owned property mini-cards or color-group summary.
- Jail/bankrupt/AI blocked status only when applicable.
- Active turn emphasis when it is that player's turn.

Current player treatment:

- Strong visual emphasis.
- Larger name/cash/action region.
- Clear "current turn" or equivalent game-facing language.
- Active player color frame, glow, banner, or table spotlight.

Opponent treatment:

- Quieter but still readable.
- Cash and ownership visible.
- No equal visual weight with the current player unless comparing players is the current task.

Player trays must not become generic white dashboard cards. They should feel like game trays,
score rails, or tabletop mats.

## Current-Turn Command Area

The current-turn command area is the primary action component.

It must answer:

- Whose turn is it?
- What phase is this?
- What just happened?
- What must be handled now?
- What legal actions can the user take?
- Why is an action disabled?

Required:

- Use `LegalAction.description` or equivalent state-derived detail in the UI.
- Show property names, prices, debt amounts, rent reasons, auction minimums, and jail choices when
  they matter.
- Surface active payment/debt state directly.
- Surface AI-controlled turns with a clear `Step AI` path.
- Explain `AI_BLOCKED`, loading, ended game, and disabled states.
- Keep legal action buttons visible and close to context.

Avoid:

- Generic button-only action labels when details are available.
- Nested action groups that add labels but no meaning.
- Repeated "Unavailable" text for expected absent actions.
- Status badges that compete with the actual next move.

## Cards, Deeds, Decks, And Reveals

Decks and cards should feel like board-game objects.

Chance and Community Chest decks:

- Look like physical card decks.
- Use original front/back art.
- Sit in the board center or nearby play surface.
- Have accessible labels.

Drawn card reveal:

- Present as a physical card reveal, not a generic modal article.
- Include deck identity, title, instruction, and optional small original art.
- Include a clear dismiss control.
- Use reduced-motion-safe reveal animation.

Property/deed details:

- Present as deed cards or compact property cards.
- Preserve game facts: price, rent, mortgage, owner, development, group.
- Use property group color and a small motif.
- Avoid raw backend field names.

Complex financial instruments:

- Use contract/deal cards when surfaced to players.
- Keep research identifiers in details or audit view, not in normal card fronts.

## Hamburger Drawer, Tabs, And Secondary Views

The hamburger must become a real game drawer.

It should own:

- Game id and game status.
- Current phase and current player summary.
- Navigation links to board, current turn, player trays, property management, negotiations,
  contracts, game log, AI notebook, and setup.
- Session utilities: save game, load game, end game, return to setup.
- Secondary inspection shortcuts: setup seed, negotiation cutoffs, player list, audit/log links.

It must not own:

- Roll dice.
- Buy property.
- Start auction.
- Bid/pass auction.
- Settle debt.
- Jail actions.
- End turn.
- Step AI when it is the active next move.
- Currently legal property-management actions.

Secondary tabs around the board may expose:

- Properties.
- Deals.
- Contracts.
- AI Notebook.
- Log.
- Rules.

Tabs must:

- Have clear selected state.
- Avoid no-op controls.
- Preserve keyboard navigation.
- Keep primary gameplay actions outside the tab system unless the tab is already active for that
  task.

## Research And Audit UI

Research features remain essential, but they must not dominate normal play.

Normal play should hide or demote:

- Raw AI output.
- Parsed JSON.
- Validation internals.
- State hashes.
- Event IDs.
- Contract IDs.
- Deal IDs.
- Rejected output IDs.
- Retrieval record IDs.
- Implementation explanatory copy.

Research/audit content should live in:

- AI Notebook drawer/tab.
- Game Log drawer/tab.
- Contract detail disclosure.
- Debug/research mode.
- Dedicated route if needed.

When research details are visible, they may use denser documentation-like styling, but they must be
visually separate from the main game surface.

## Visual Style System

The visual system should feel like an original tabletop game.

Required style qualities:

- Top-down 2D clarity.
- Tactile paper/card surfaces.
- Strong board outline.
- Compact symbols.
- Clear player colors.
- Game-mat page background.
- Purposeful shadows and elevation.
- Minimal administrative chrome.

Avoid:

- Wooden table background as the primary page surface.
- Generic SaaS/dashboard white-card layout.
- Blue/green ownership wash over property tiles.
- One-note beige-only palette.
- Overuse of rounded pills.
- Equal visual weight for every panel.
- Decorative badges that do not communicate current game state.

Typography:

- Board title may use a distinct local/system display treatment.
- Board labels must remain legible at board scale.
- Player tray cash and current-player name need stronger hierarchy than status labels.
- Replace repeated uppercase metadata labels with visible game-facing organization where possible.
- Do not use official Monopoly-like lettering or copied typography.

Icons:

- Use icons for actions, deck identity, ownership, development, token types, money, and tabs.
- Prefer lucide icons for general UI controls where suitable.
- Use custom local icons for board-game-specific pieces when lucide is too generic.
- Icon-only controls require accessible names and tooltips where meaning is not obvious.

Motion:

- Dice roll, token movement, card reveal, active-player change, and payment settlement may animate.
- Motion should clarify state changes, not decorate passive content.
- Respect `prefers-reduced-motion`.

## Asset Structure

Use this structure for art assets:

- `assets/art/board/`: board-square, owner-marker, development-marker, board-center, and table-mat
  production art.
- `assets/art/cards/`: Chance, Community Chest, property/deed, contract/deal, and deck-back art.
- `assets/art/tokens/`: player token silhouettes and token variants.
- `assets/art/ui/`: game drawer, tab, icon, status, and tray support art when not better handled
  through code.
- `assets/art/prompts/`: prompt records for generated art.
- `assets/art/reviews/`: human/Codex review notes for generated art, artifact acceptance, and IP
  checks.
- `assets/vector/`: legacy and reusable vector support assets.

Existing `assets/vector` files remain valid support assets, but they are no longer the only
permitted finished art format.

## AI-Generated Art Policy

AI-generated original bitmap art is allowed.

AI-generated art must:

- Be created specifically for this project.
- Be stored locally in the repository before use.
- Have a prompt record in `assets/art/prompts/`.
- Have a review record in `assets/art/reviews/` before production use.
- Be checked for unwanted text, watermarks, logos, copied mascots, copied board scans, copied
  typography, and unreadable small-size details.

Generated art may be:

- `.png`
- `.webp`
- `.avif`
- `.svg` after vectorization
- React/SVG code derived from an accepted concept

Tiny board-square art may use simplified SVG or CSS motifs when that renders more clearly than a
detailed bitmap. Richer bitmap work is more appropriate for card fronts, title art, table-mat
texture, and larger reveal views.

## Implementation Sequence For Autonomous Codex Work

This sequence is the recommended order for a Codex Goal process. Write or update tests before each
implementation pass.

### Pass 1: Lock The New Art Contract In Tests

Update or add tests that fail against the current dashboard-like implementation:

- All 40 board spaces render.
- Non-street board spaces render visible motif art.
- Street properties assert absence of `[data-space-art]`.
- E2E motif count expects 18 non-street visible motifs.
- Board center contains only game-facing title/deck/dice/card/winner content.
- No board-center debug or implementation copy.
- Hamburger opens and closes a real drawer/disclosure.
- Primary actions remain outside the hamburger.
- First desktop viewport contains board, current player, cash, and primary legal action.
- Mobile viewport contains board first and current-turn command area immediately after.
- Research/audit panels are not all visible by default during normal play.

### Pass 2: Establish Game-Surface Layout

Refactor the active game page so that:

- Board is the dominant layout object.
- Current-turn command area is adjacent to board.
- Player trays replace or visually supersede generic player/status panels.
- Secondary panels move to drawer, tabs, or collapsible trays.
- The hamburger has access to session utilities and secondary navigation.

Primary files likely involved:

- `apps/web/app/game-play-surface.tsx`
- `apps/web/app/game-table-menu.tsx`
- `apps/web/app/games/[gameId]/page.tsx`
- `apps/web/app/globals.css`
- Existing component tests and Playwright specs.

### Pass 3: Complete Board Art

Implement:

- Owner markers.
- Development markers.
- Improved token pieces.
- Refined center title/deck treatment.
- Cleaner board-space detail hover/focus cards.

Primary files likely involved:

- `apps/web/app/game-board.tsx`
- `apps/web/app/board-art.tsx`
- `assets/art/board/`
- `assets/art/tokens/`
- `assets/vector/`
- `apps/web/app/game-board.test.tsx`
- `apps/web/e2e/game-board.spec.ts`

### Pass 4: Player Trays And Current-Turn Clarity

Implement:

- Active current-player tray.
- Opponent trays.
- Cash display.
- Token display.
- Owned color-group or mini-card summary.
- Active payment/debt attention panel.
- Legal action descriptions in controls.
- Disabled-state explanations.

Primary files likely involved:

- `apps/web/app/game-play-surface.tsx`
- `apps/web/app/property-management.tsx`
- `apps/web/app/auction-panel.tsx`
- `apps/web/app/turn-controls.test.tsx`
- `apps/web/e2e/stage-11-3-playability-review.spec.ts`

### Pass 5: Cards, Deals, Contracts, And AI Notebook Demotion

Implement:

- Drawn-card reveal as physical card.
- Property/deed card presentation.
- Contract/deal cards for normal player-facing view.
- AI Notebook tab/drawer for research detail.
- Game Log tab/drawer for audit detail.
- Removal of raw IDs from normal play surfaces.

Primary files likely involved:

- `apps/web/app/game-board.tsx`
- `apps/web/app/negotiation-panel.tsx`
- `apps/web/app/contracts-panel.tsx`
- `apps/web/app/ai-audit-panel.tsx`
- `assets/art/cards/`

### Pass 6: Responsive And Visual Verification

Add or update Playwright coverage:

- Desktop board-first layout.
- Tablet layout.
- Mobile board-first layout.
- Hamburger drawer open/close/focus behavior.
- No incoherent overlaps.
- Text fit in board labels, trays, buttons, tabs, and cards.
- Player tray stress state with 5 players.
- Long player names.
- Multiple tokens on one space.
- AI thinking state.
- Rejected-action state.
- Negotiation and contract-heavy state.
- Game-over winner state.

Use screenshots or bounding-box checks when layout quality cannot be proven through DOM assertions
alone.

## File And Component Targets

Expected current problem areas:

- `apps/web/app/game-play-surface.tsx`: dashboard-like layout, repeated panels, generic turn controls,
  session panel, stale rejection surfacing, and always-mounted support panels.
- `apps/web/app/game-board.tsx`: board presentation, street motif gap, token visuals, card reveal,
  center treatment, hover/focus details.
- `apps/web/app/board-art.tsx`: motif metadata and rendering primitives.
- `apps/web/app/game-table-menu.tsx`: underpowered hamburger menu.
- `apps/web/app/dashboard-shell.tsx`: setup/home page still reads as operational dashboard.
- `apps/web/app/game-setup.tsx`: dense setup table instead of game-seat setup.
- `apps/web/app/property-management.tsx`: full catalog buries legal property moves.
- `apps/web/app/contracts-panel.tsx`: normal surfaces expose internal identifiers.
- `apps/web/app/ai-audit-panel.tsx`: research details are too prominent for normal play.
- `apps/web/app/globals.css`: visual tokens and game-surface styling.
- `apps/web/playwright.config.ts`: currently needs stronger mobile/tablet visual coverage.

## Test And Verification Requirements

Every art-direction implementation pass must use TDD:

1. Add or update failing tests.
2. Implement the smallest coherent visual/structural change.
3. Run the relevant targeted tests.
4. Run broader frontend tests when a shared component or layout changes.
5. Document residual visual risks in the final response.

Required test categories:

- Unit/component tests for board metadata, motifs, card reveal, trays, drawer behavior, and legal
  action descriptions.
- Integration/component tests for current-turn state, AI disabled states, active payment/debt, and
  property-management action discovery.
- End-to-end tests for desktop playability, mobile playability, drawer behavior, and visual layout
  constraints.
- Smoke tests that still prove local game creation and turn advancement.
- Regression tests for fixed UX defects.

Minimum visual assertions:

- Board is visible and dominant in first viewport.
- Current player is visually and semantically identifiable.
- Cash is visible for every player or through an immediately available tray/chip.
- Token positions are visible.
- Ownership is visible without recoloring whole property tiles.
- Development is visible on property spaces.
- Legal action details are visible when the action has consequences.
- Hamburger opens, closes, and contains session utilities.
- Debug/audit internals are not visible by default in normal play.
- Mobile layout does not bury primary action below long support content.

## Acceptance Checklist

The art direction is complete only when all items are true:

- The game looks like a finished 2D tabletop video game at desktop, tablet, and mobile widths.
- The board is the hero object of the play page.
- The center is simple, game-facing, and not busy.
- Non-street board spaces have visible original motif art.
- Street property tiles preserve names, prices, and group colors without per-square motif art.
- Ownership is clear through owner markers, not whole-tile owner recoloring.
- Development is clear directly on property spaces.
- Player trays make cash, token, ownership, and current player clear at a glance.
- Primary legal actions are close to the board and use meaningful descriptions.
- Active payment/debt and AI blocked/thinking states are obvious.
- The hamburger drawer is useful and owns secondary navigation plus session utilities.
- Research/audit surfaces are available but demoted from normal play.
- Drawn cards and property/deed details look like game artifacts.
- Tokens look like distinct game pieces.
- Text fits and remains readable.
- Focus states and keyboard access work.
- Reduced-motion mode is respected.
- All artwork is local, original, reviewed where generated, and free of copied IP.
- The relevant unit, component, E2E, smoke, and regression tests pass.

## Prohibited Final Outcomes

The implementation is not acceptable if:

- It still reads as a generic admin dashboard.
- It shows all audit/research panels by default in normal play.
- The hamburger remains a one-link setup menu.
- Non-street board-space motif art is missing or unreadable.
- Street properties reintroduce per-square motif art that competes with names, prices, tokens, or
  markers.
- Current player, cash, ownership, development, or legal next action require scanning long panels.
- Whole property tiles are recolored by owner in a way that duplicates or obscures group colors.
- The board center becomes visually busy or filled with scenery/logs/debug text.
- Official Monopoly visuals, typography, wording, mascot-like art, or board scans are copied.
- Tests preserve the old dashboard-first expectations.

## Working Summary For Future Codex

Do not polish the existing dashboard. Replace its hierarchy.

Build a board-first game table:

- Board in the middle.
- Player trays around it.
- Current turn beside it.
- Ownership and development on the board.
- Cards and tokens as game pieces.
- Hamburger as a real drawer.
- Research views behind secondary affordances.

The goal is not more decoration. The goal is immediate game comprehension.
