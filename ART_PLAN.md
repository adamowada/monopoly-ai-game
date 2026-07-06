# ART_PLAN.md

## Authority

`ART_PLAN.md` is the authoritative art-direction plan for this repository. It supersedes older
visual-asset requirements in `PLANS.md` that limited finished artwork to SVG/vector-only assets.

All game visuals must still be original local assets. Do not copy official Monopoly board scans,
logos, mascot art, card artwork, typography, screenshots, or downloaded protected artwork.

## Working Motif

The temporary working motif is `Monopoly 2.0` until a more original game title is chosen.

Use the phrase as a plain temporary title mark, not as an imitation of the official Monopoly wordmark.
Avoid official Monopoly trade dress: no red wordmark plaque, no Mr. Monopoly-like mascot, no copied
board-center layout, no copied card illustrations, and no official card or rule wording.

## Art Direction

The finished game should read as a real tabletop game, not a research UI.

- The board is the hero object of the play screen.
- The board center contains a custom `Monopoly 2.0` title mark plus Chance and Community Chest decks.
- Remove board-center implementation copy such as backend state explanations, stable index notes, and
  vector-only disclaimers.
- Each square has a compact original visual motif that fits at board scale.
- Property/deed cards and drawn Chance/Community Chest cards can use richer art than the small board
  squares because they have more room.
- Keep property group colors and classic space names for the current compatibility theme.
- Prefer player-facing text on the board: square name, price where relevant, deck label, tax amount,
  and token markers.
- Keep developer/debug information out of the board surface unless a dedicated debug mode is added.

## AI-Generated Art Policy

AI-generated original bitmap art is allowed for this project.

- AI-generated art must be created for this project and stored locally in the repository.
- Generated art may be raster (`.png`, `.webp`, `.avif`) or converted/vectorized into SVG when that
  serves the UI better.
- Generated art must not include third-party logos, official Monopoly marks, copied mascots, board
  scans, watermarks, or copied typography.
- Generated art must be reviewed before use for text artifacts, unwanted logos, confusing symbols,
  and poor small-size readability.
- Tiny board-square art may use simplified SVG or CSS motifs when that renders more clearly than a
  detailed bitmap. The source prompt/concept still belongs to this art plan.

## Asset Structure

Use this structure for new art assets:

- `assets/art/board/`: board-square and board-center production art.
- `assets/art/cards/`: Chance, Community Chest, property/deed, and deck-back art.
- `assets/art/prompts/`: prompt records for generated art.
- `assets/vector/`: legacy and reusable vector support assets.

Existing `assets/vector` files remain valid support assets, but they are no longer the only permitted
finished art format.

## Implementation Requirements

- The board component must render a visual motif for every one of the 40 spaces.
- The board component must render custom center title art and deck art.
- The board component must not render research-console copy in the board center.
- Board-square visuals must have accessible labels or be hidden when redundant to visible text.
- Tests must assert that all board spaces have art metadata and that the center presentation is
  game-facing.

## Review Checklist

- The board looks like a finished game surface at desktop and mobile widths.
- Every square has a visible visual identity.
- Property group colors remain recognizable.
- The center art reads as custom/local and does not mimic official Monopoly trade dress.
- Chance and Community Chest decks look like physical card decks.
- No board-center copy references implementation internals.
- All artwork is local, original, and either generated for this project or authored in repo-native
  code/vector form.
