# Vector Artwork

This directory contains original local vector artwork for the Monopoly-style research game.

These assets were authored for this repository. They use no downloaded scans or downloaded art, and
they do not use official Monopoly artwork, logos, mascot imagery, board scans, or protected
typography. The property color bands follow the classic group idea in a generic way, but all drawing
geometry, labels, token shapes, and card treatments are original.

All SVG files in this directory must remain vector-only. Do not add embedded raster elements,
base64-encoded raster payloads, remote references, or copied artwork.

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

The Next.js board currently renders inline SVG/CSS vector shapes from game state. These standalone
SVG files document and support the asset direction for reuse in later UI stages.
