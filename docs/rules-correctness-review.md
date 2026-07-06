# Phase 11 Stage 11.1 Rules Correctness Review

Date: 2026-07-06

Scope: deterministic classic rules review for the backend rules engine, static data, action validation, reducer, and public action path. Reviewed areas were jail, auctions, mortgages, bankruptcy, card effects, and house/hotel scarcity.

No unimplemented required mechanics.

## Classic Mechanics Checklist

| Mechanic | Status | Evidence |
| --- | --- | --- |
| 2-5 player setup and classic starting cash | Implemented | `create_initial_game_state` creates 2-5 players with 1500 cash. |
| 40-space classic board and 28 purchasable properties | Implemented | Static data validation enforces board order, unique IDs, and property/space consistency. |
| Classic property prices, rents, mortgage values, groups, house costs, and hotel costs | Implemented | Static data validation and rent tests cover streets, railroads, and utilities. |
| 32 houses and 12 hotels | Implemented | Static bank inventory validation and scarcity invariants enforce the supply. |
| Passing GO | Implemented | Movement pays 200 when forward movement crosses or lands beyond GO. |
| Dice rolls and deterministic RNG | Implemented | Accepted dice events store dice values and roll counters. |
| Doubles and triple-doubles jail behavior | Implemented | Doubles count is tracked; third consecutive doubles sends the player to jail. |
| Jail roll attempts, doubles release, mandatory third-failure fine, fine payment, and jail-card use | Implemented | Jail mechanics and legal actions expose roll, fine, and card choices. |
| Buying unowned property | Implemented | Roll landing now enters `PURCHASE_OR_AUCTION`; buying advances to post-roll management. |
| Auctions | Implemented | Start, bid, pass, automatic close, no-bid close, winner payment, and property transfer are enforced. |
| Rent calculation and payment | Implemented | Landing on owned property creates structured active debt; settlement transfers cash or permits bankruptcy. |
| Taxes | Implemented | Landing on tax spaces creates structured bank debt. |
| Chance and Community Chest card effects | Implemented | Card draw events, deck counters, movement, bank payments, player transfers, repairs, jail cards, and go-to-jail effects are enforced. |
| Mortgages and unmortgages | Implemented | Mortgages pay mortgage value; unmortgages charge principal plus 10% rounded up. |
| Even building rule | Implemented | Buy/sell improvement checks enforce even build and reverse-even sale rules. |
| Selling houses and hotels | Implemented | Sell actions pay half house cost and update bank inventory. |
| House/hotel scarcity | Implemented | Buying/selling checks bank inventory; invariants reconcile bank plus board supply. |
| Bankruptcy and asset liquidation/transfer | Implemented | Bankruptcy to bank liquidates assets; bankruptcy to creditor transfers assets and cash; active debt is cleared. |
| Game-over and winner detection | Implemented | Game over is true when one or fewer active players remain. |

## Known Deviations

- AI failures are not resolved by fallback moves. This is an intentional local-project deviation required by `PLANS.md`; invalid or unavailable AI output is rejected or can block mandatory AI action flow.
- Structured negotiations and financial instruments extend classic Monopoly trading. This is intentional local-project functionality and does not replace required classic mechanics.
- Negotiation windows can expire through deterministic cutoff rules. Expiration does not invent a move, transfer assets, or bypass backend validation.
- Nearest-utility Chance rent uses the triggering deterministic roll total when resolved through the public roll path, rather than introducing an extra dice roll prompt. This preserves deterministic no-fallback action flow while still enforcing the special 10x utility card rent.

## Edge-Case Review

### Jail

Reviewed triple-doubles, sent-to-jail position, jail turn counters, doubles release, third failed roll fine, explicit fine payment, get-out-of-jail card use, and bankruptcy while jailed. Stage 11.1 added regression coverage that landing on Go To Jail through `ROLL_DICE` moves the player to jail and opens a post-roll end-turn path.

### Auctions

Reviewed auction start on unowned property, minimum bids, self-overbid rejection, pass tracking, passed-player rejection, high bidder cannot pass, no-bid close, final bidder close, winner payment, and ownership transfer. Stage 11.1 fixed the resolved auction path so a closed auction returns to post-roll management instead of leaving the turn stranded in `PURCHASE_OR_AUCTION`.

### Mortgages

Reviewed owner validation, duplicate mortgage rejection, unmortgage cost, insufficient cash rejection, rent suppression while mortgaged, and the rule that no property in a color group may be mortgaged while that group has improvements. No missing mortgage mechanic was found.

### Bankruptcy

Reviewed bankruptcy to bank, bankruptcy to creditor, active debt creditor inference, debt clearing, asset transfer, improvement liquidation, cash transfer, jail state clearing, held jail-card return, and game-over detection. Stage 11.1 added regression coverage for rent debt leading to bankruptcy without leaving active payment behind.

### Card effects

Reviewed deck data, card draw counters, discard handling, movement cards, nearest railroad/utility cards, bank payments, player payments, building repairs, jail cards, and go-to-jail cards. Stage 11.1 fixed public roll card resolution and get-out-of-jail card accounting so held cards are removed from discard while held and returned when used or released by bankruptcy.

### House scarcity

Reviewed even build, reverse-even sell, hotel conversion, hotel sellback, no-house/no-hotel bank states, bank inventory bounds, and invariant reconciliation. No missing scarcity mechanic was found.

## Stage 11.1 Fixes

- Public `ROLL_DICE` now resolves landed spaces instead of always advancing to post-roll management.
- Unowned property landings now open `PURCHASE_OR_AUCTION`.
- Owned property landings now create structured rent debt through `ACTIVE_PAYMENT_SET`.
- Tax landings now create structured bank debt.
- Go To Jail landings now send the player to jail.
- Chance and Community Chest landings now draw and apply cards through accepted events.
- Buying property, settling payment, and resolved auctions now complete their timing windows.
- Held get-out-of-jail cards are no longer duplicated in deck discard and are returned to discard when used or released by bankruptcy.

## Regression Coverage

Added `services/api/tests/test_stage_11_1_rules_correctness.py` with focused regression tests for roll landing resolution, rent debt and bankruptcy, jail, card effects, mortgage/build scarcity, and auction close behavior. Existing `test_actions.py` was updated to assert the corrected post-roll purchase window.
