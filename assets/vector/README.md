# Vector Artwork

This directory contains original local vector support artwork for the Monopoly-style game.

These assets were authored for this repository. They use no downloaded scans or downloaded art, and
they do not use official Monopoly artwork, logos, mascot imagery, board scans, or protected
typography. The property color bands follow the classic group idea in a generic way, but all drawing
geometry, labels, token shapes, and card treatments are original.

`ART_PLAN.md` is the authoritative art-direction plan. It allows original local AI-generated bitmap
art for finished game visuals. SVG files that remain in this directory should stay vector-only: do
not add embedded raster elements, base64-encoded raster payloads, remote references, or copied
artwork to these SVG support files.

## Files

- `board.svg`: original 40-space board reference with property color bands.
- `card-back-chance.svg`: original Chance deck back.
- `card-front-chance.svg`: original Chance deck front.
- `card-back-community-chest.svg`: original Community Chest deck back.
- `card-front-community-chest.svg`: original Community Chest deck front.
- `token-set.svg`: original abstract player token marker set.
- `house.svg`: original house improvement marker.
- `hotel.svg`: original hotel improvement marker.
- `ownership-marker.svg`: original ownership marker.
- `mortgage-marker.svg`: original mortgage marker.

The Next.js board renders local code-native motifs from game state and may also consume local bitmap
art from `assets/art` as the art direction develops.
