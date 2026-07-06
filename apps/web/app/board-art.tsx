import type { DeckName, SpaceId } from "@monopoly-ai-game/schemas";

export type SpaceArtMotif =
  | "go-arrow"
  | "brown-courtyard"
  | "community-chest"
  | "brown-terrace"
  | "tax-ledger"
  | "rail-station"
  | "garden-gate"
  | "chance-burst"
  | "maple-walk"
  | "river-row"
  | "jail-keys"
  | "theater-awning"
  | "utility-tower"
  | "state-house"
  | "glass-avenue"
  | "rail-bridge"
  | "music-corner"
  | "market-stalls"
  | "city-skyline"
  | "parking-car"
  | "racehorse"
  | "state-seal"
  | "museum-front"
  | "harbor-rail"
  | "ocean-arch"
  | "wind-sail"
  | "water-tower"
  | "garden-estate"
  | "jail-wagon"
  | "palm-avenue"
  | "carolina-pines"
  | "quill-house"
  | "shortline-switch"
  | "park-lamps"
  | "luxury-ring"
  | "boardwalk-pier";

export type SpaceArt = {
  readonly title: string;
  readonly motif: SpaceArtMotif;
  readonly palette: readonly [string, string, string];
};

export type DeckArt = {
  readonly title: string;
  readonly tone: "chance" | "community";
  readonly palette: readonly [string, string, string];
};

const paper = "#fff3cf";
const ink = "#2f2418";

export const SPACE_ART_BY_ID: Readonly<Record<SpaceId, SpaceArt>> = {
  space_go: { title: "Start arrow seal", motif: "go-arrow", palette: ["#f7d56f", "#fef7df", "#0f766e"] },
  space_mediterranean_avenue: { title: "Mediterranean courtyard", motif: "brown-courtyard", palette: ["#7c3f24", paper, "#d6a15c"] },
  space_community_chest_1: { title: "Oak community chest", motif: "community-chest", palette: ["#1d4f77", "#d8eef8", "#b98742"] },
  space_baltic_avenue: { title: "Baltic brick terrace", motif: "brown-terrace", palette: ["#6e321f", "#f1d1a8", "#a86436"] },
  space_income_tax: { title: "Income tax ledger", motif: "tax-ledger", palette: ["#0f766e", "#fff7d6", "#c28a2e"] },
  space_reading_railroad: { title: "Reading station clock", motif: "rail-station", palette: ["#171717", "#f5f1df", "#9b6a32"] },
  space_oriental_avenue: { title: "Oriental garden gate", motif: "garden-gate", palette: ["#8fd7ec", "#f6fbff", "#2b8fac"] },
  space_chance_1: { title: "Chance burst card", motif: "chance-burst", palette: ["#e06a2f", "#fff1c8", "#9b2f18"] },
  space_vermont_avenue: { title: "Vermont maple walk", motif: "maple-walk", palette: ["#8fd7ec", "#fff7dc", "#ba6b24"] },
  space_connecticut_avenue: { title: "Connecticut river row", motif: "river-row", palette: ["#8fd7ec", "#e7f6fb", "#26738c"] },
  space_jail: { title: "Just visiting keys", motif: "jail-keys", palette: ["#c86c24", "#f9e2bf", "#2e2a24"] },
  space_st_charles_place: { title: "St. Charles theater awning", motif: "theater-awning", palette: ["#b84a87", "#ffe4f1", "#61224b"] },
  space_electric_company: { title: "Electric utility tower", motif: "utility-tower", palette: ["#d9d7d0", "#fff8d7", "#45515b"] },
  space_states_avenue: { title: "States Avenue house", motif: "state-house", palette: ["#b84a87", "#f6e6f2", "#81416c"] },
  space_virginia_avenue: { title: "Virginia glass avenue", motif: "glass-avenue", palette: ["#b84a87", "#ffe5f3", "#2e6a78"] },
  space_pennsylvania_railroad: { title: "Pennsylvania rail bridge", motif: "rail-bridge", palette: ["#171717", "#f3efe1", "#6b7280"] },
  space_st_james_place: { title: "St. James music corner", motif: "music-corner", palette: ["#e8772e", "#fff0d6", "#80411c"] },
  space_community_chest_2: { title: "Blue community chest", motif: "community-chest", palette: ["#1f65a7", "#dbeafe", "#b9853a"] },
  space_tennessee_avenue: { title: "Tennessee market stalls", motif: "market-stalls", palette: ["#e8772e", "#fff2d8", "#9a4a19"] },
  space_new_york_avenue: { title: "New York skyline", motif: "city-skyline", palette: ["#e8772e", "#ffefd1", "#36506c"] },
  space_free_parking: { title: "Free parking sedan", motif: "parking-car", palette: ["#cf4b2f", "#ffe8d8", "#274c3f"] },
  space_kentucky_avenue: { title: "Kentucky racehorse", motif: "racehorse", palette: ["#d83f3f", "#ffe0df", "#7a1f22"] },
  space_chance_2: { title: "Chance compass burst", motif: "chance-burst", palette: ["#e06a2f", "#fff1c8", "#9b2f18"] },
  space_indiana_avenue: { title: "Indiana state seal", motif: "state-seal", palette: ["#d83f3f", "#ffe5df", "#9a2b2f"] },
  space_illinois_avenue: { title: "Illinois museum front", motif: "museum-front", palette: ["#d83f3f", "#fff0df", "#274c67"] },
  space_b_and_o_railroad: { title: "B&O harbor rail", motif: "harbor-rail", palette: ["#171717", "#f3ead5", "#31495f"] },
  space_atlantic_avenue: { title: "Atlantic ocean arch", motif: "ocean-arch", palette: ["#f2c94c", "#fff6c9", "#1f6f86"] },
  space_ventnor_avenue: { title: "Ventnor wind sail", motif: "wind-sail", palette: ["#f2c94c", "#fff7cd", "#5b6f89"] },
  space_water_works: { title: "Water works tower", motif: "water-tower", palette: ["#d9d7d0", "#e3f7ff", "#256b85"] },
  space_marvin_gardens: { title: "Marvin garden estate", motif: "garden-estate", palette: ["#f2c94c", "#fff5c8", "#386641"] },
  space_go_to_jail: { title: "Go to jail wagon", motif: "jail-wagon", palette: ["#c86c24", "#ffe2bf", "#2f2b24"] },
  space_pacific_avenue: { title: "Pacific palm avenue", motif: "palm-avenue", palette: ["#26824a", "#e3f7dc", "#2e6041"] },
  space_north_carolina_avenue: { title: "North Carolina pines", motif: "carolina-pines", palette: ["#26824a", "#e1f5d8", "#1f5538"] },
  space_community_chest_3: { title: "Community quill chest", motif: "quill-house", palette: ["#1f65a7", "#dbeafe", "#93662e"] },
  space_pennsylvania_avenue: { title: "Pennsylvania avenue lamps", motif: "park-lamps", palette: ["#26824a", "#e4f5db", "#2a4f35"] },
  space_short_line_railroad: { title: "Short Line switch track", motif: "shortline-switch", palette: ["#171717", "#f4ecdb", "#7a5c36"] },
  space_chance_3: { title: "Chance question seal", motif: "chance-burst", palette: ["#e06a2f", "#fff1c8", "#9b2f18"] },
  space_park_place: { title: "Park Place lamps", motif: "park-lamps", palette: ["#2f5597", "#dbeafe", "#102a4e"] },
  space_luxury_tax: { title: "Luxury tax ring", motif: "luxury-ring", palette: ["#b91c1c", "#ffe7df", "#c79b38"] },
  space_boardwalk: { title: "Boardwalk pier", motif: "boardwalk-pier", palette: ["#1b3f8b", "#dcecff", "#8b5a2b"] },
};

export const DECK_ART: Readonly<Record<DeckName, DeckArt>> = {
  chance: { title: "Chance", tone: "chance", palette: ["#d9552b", "#fff2ca", "#9a2b1d"] },
  community_chest: { title: "Community Chest", tone: "community", palette: ["#1f5f9b", "#dbeafe", "#b9853a"] },
};

type SpaceMotifProps = {
  readonly art: SpaceArt;
  readonly className?: string;
};

function Skyline({ color }: { readonly color: string }) {
  return (
    <>
      <rect x="18" y="42" width="10" height="24" rx="1.5" fill={color} opacity="0.88" />
      <rect x="32" y="31" width="11" height="35" rx="1.5" fill={color} opacity="0.72" />
      <rect x="47" y="37" width="12" height="29" rx="1.5" fill={color} opacity="0.82" />
      <rect x="63" y="26" width="10" height="40" rx="1.5" fill={color} opacity="0.68" />
      <path d="M14 66 H78" stroke={color} strokeWidth="4" strokeLinecap="round" />
    </>
  );
}

function Rails({ color }: { readonly color: string }) {
  return (
    <>
      <path d="M18 60 C33 48 55 48 74 60" fill="none" stroke={color} strokeWidth="5" strokeLinecap="round" />
      <path d="M22 68 C37 57 55 57 70 68" fill="none" stroke={color} strokeWidth="3" strokeLinecap="round" opacity="0.65" />
      {[28, 38, 48, 58, 68].map((x) => (
        <path key={x} d={`M${x} 54 L${x - 7} 68`} stroke={color} strokeWidth="2.5" strokeLinecap="round" />
      ))}
    </>
  );
}

function HouseRow({ color, accent }: { readonly color: string; readonly accent: string }) {
  return (
    <>
      <path d="M18 58 H74 V70 H18 Z" fill={color} opacity="0.86" />
      <path d="M16 58 L30 44 L44 58 M42 58 L56 44 L76 58" fill="none" stroke={accent} strokeWidth="4" strokeLinejoin="round" />
      <rect x="28" y="61" width="8" height="9" fill={accent} opacity="0.86" />
      <rect x="56" y="61" width="8" height="9" fill={accent} opacity="0.86" />
    </>
  );
}

function Tree({ color, accent }: { readonly color: string; readonly accent: string }) {
  return (
    <>
      <path d="M46 68 V38" stroke={accent} strokeWidth="5" strokeLinecap="round" />
      <path d="M46 28 C35 38 28 48 23 62 C37 59 45 54 46 42 C48 55 57 61 70 62 C64 47 57 37 46 28 Z" fill={color} opacity="0.84" />
    </>
  );
}

function Vehicle({ color, accent }: { readonly color: string; readonly accent: string }) {
  return (
    <>
      <path d="M20 57 C26 45 33 40 48 40 C62 40 70 45 76 57 L72 65 H24 Z" fill={color} opacity="0.9" />
      <path d="M34 42 L42 31 H57 L66 42" fill="none" stroke={accent} strokeWidth="4" strokeLinecap="round" strokeLinejoin="round" />
      <circle cx="34" cy="66" r="6" fill={accent} />
      <circle cx="64" cy="66" r="6" fill={accent} />
    </>
  );
}

function QuestionMark({ color, accent }: { readonly color: string; readonly accent: string }) {
  return (
    <>
      <path d="M45 56 C45 45 60 45 60 33 C60 23 51 17 42 17 C32 17 25 22 22 31" fill="none" stroke={color} strokeWidth="8" strokeLinecap="round" />
      <circle cx="45" cy="71" r="5.5" fill={color} />
      <path d="M18 22 L24 18 M68 20 L75 14 M18 70 L11 76 M72 70 L81 76" stroke={accent} strokeWidth="3" strokeLinecap="round" />
    </>
  );
}

function Chest({ color, accent }: { readonly color: string; readonly accent: string }) {
  return (
    <>
      <path d="M21 43 H73 V68 H21 Z" fill={color} opacity="0.92" />
      <path d="M24 43 C27 29 37 22 47 22 C58 22 68 29 70 43" fill="none" stroke={color} strokeWidth="8" strokeLinecap="round" />
      <path d="M21 52 H73 M47 42 V68" stroke={accent} strokeWidth="4" />
      <circle cx="47" cy="55" r="4" fill={accent} />
    </>
  );
}

export function SpaceMotif({ art, className = "" }: SpaceMotifProps) {
  const [main, background, accent] = art.palette;

  function motifShape() {
    switch (art.motif) {
      case "go-arrow":
        return (
          <>
            <path d="M20 50 H60" stroke={main} strokeWidth="9" strokeLinecap="round" />
            <path d="M52 30 L74 50 L52 70" fill="none" stroke={main} strokeWidth="9" strokeLinecap="round" strokeLinejoin="round" />
            <circle cx="25" cy="26" r="7" fill={accent} opacity="0.75" />
          </>
        );
      case "community-chest":
      case "quill-house":
        return <Chest color={main} accent={accent} />;
      case "chance-burst":
        return <QuestionMark color={main} accent={accent} />;
      case "rail-station":
      case "rail-bridge":
      case "harbor-rail":
      case "shortline-switch":
        return <Rails color={main} />;
      case "parking-car":
      case "jail-wagon":
        return <Vehicle color={main} accent={accent} />;
      case "utility-tower":
        return (
          <>
            <path d="M46 18 L25 70 H67 Z" fill="none" stroke={main} strokeWidth="5" strokeLinejoin="round" />
            <path d="M34 48 H58 M38 37 H54 M31 60 H61" stroke={main} strokeWidth="4" strokeLinecap="round" />
            <circle cx="46" cy="18" r="6" fill={accent} />
          </>
        );
      case "water-tower":
        return (
          <>
            <ellipse cx="47" cy="33" rx="21" ry="12" fill={main} opacity="0.88" />
            <path d="M30 42 H64 L59 67 H35 Z" fill="none" stroke={main} strokeWidth="5" strokeLinejoin="round" />
            <path d="M34 67 H60 M38 51 H56" stroke={accent} strokeWidth="4" strokeLinecap="round" />
          </>
        );
      case "tax-ledger":
      case "luxury-ring":
        return (
          <>
            <path d="M25 25 H66 V66 H25 Z" fill={background} stroke={main} strokeWidth="5" />
            <path d="M33 38 H58 M33 49 H58 M33 60 H50" stroke={main} strokeWidth="3" strokeLinecap="round" opacity="0.75" />
            <circle cx="66" cy="65" r="12" fill={accent} opacity="0.82" />
          </>
        );
      case "jail-keys":
        return (
          <>
            <circle cx="35" cy="35" r="11" fill="none" stroke={main} strokeWidth="5" />
            <path d="M43 43 L72 72 M58 58 L66 50 M64 64 L73 57" stroke={main} strokeWidth="5" strokeLinecap="round" />
            <path d="M21 70 H72" stroke={accent} strokeWidth="4" strokeLinecap="round" opacity="0.72" />
          </>
        );
      case "city-skyline":
      case "museum-front":
        return <Skyline color={main} />;
      case "garden-gate":
      case "maple-walk":
      case "palm-avenue":
      case "carolina-pines":
      case "garden-estate":
        return <Tree color={main} accent={accent} />;
      case "river-row":
      case "wind-sail":
      case "ocean-arch":
      case "boardwalk-pier":
        return (
          <>
            <path d="M16 62 C30 54 42 70 56 61 C65 55 72 58 80 64" fill="none" stroke={main} strokeWidth="5" strokeLinecap="round" />
            <path d="M23 45 H72 M30 52 H66 M37 59 H60" stroke={accent} strokeWidth="4" strokeLinecap="round" />
            <path d="M50 26 L64 48 H36 Z" fill={main} opacity="0.78" />
          </>
        );
      case "racehorse":
        return (
          <>
            <path d="M22 57 C31 38 54 36 70 48 C62 49 57 55 55 68 H45 C44 58 37 57 31 68 H23 C24 63 23 60 22 57 Z" fill={main} opacity="0.88" />
            <path d="M65 47 C70 38 77 40 79 47" fill="none" stroke={accent} strokeWidth="4" strokeLinecap="round" />
          </>
        );
      case "state-seal":
        return (
          <>
            <circle cx="47" cy="47" r="27" fill="none" stroke={main} strokeWidth="6" />
            <path d="M47 24 L53 40 L70 41 L56 51 L61 67 L47 57 L33 67 L38 51 L24 41 L41 40 Z" fill={accent} />
          </>
        );
      case "theater-awning":
      case "music-corner":
      case "market-stalls":
      case "state-house":
      case "glass-avenue":
      case "brown-courtyard":
      case "brown-terrace":
      default:
        return <HouseRow color={main} accent={accent} />;
    }
  }

  return (
    <svg
      aria-label={`${art.title} motif`}
      className={className}
      data-space-art=""
      role="img"
      viewBox="0 0 96 96"
    >
      <rect x="8" y="8" width="80" height="80" rx="15" fill={background} />
      <path d="M18 18 H78 V78 H18 Z" fill="none" stroke={ink} strokeOpacity="0.18" strokeWidth="3" />
      {motifShape()}
      <path d="M16 20 C33 14 61 14 80 24" fill="none" stroke={accent} strokeOpacity="0.38" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

type DeckArtPreviewProps = {
  readonly deck: DeckArt;
};

export function DeckArtPreview({ deck }: DeckArtPreviewProps) {
  const [main, background, accent] = deck.palette;
  const isChance = deck.tone === "chance";

  return (
    <div
      aria-label={`${deck.title} deck art`}
      className="relative min-h-0 rounded border-2 p-2 shadow-[0_10px_18px_rgba(47,36,24,0.18)]"
      role="img"
      style={{
        backgroundColor: background,
        borderColor: main,
        color: main,
      }}
    >
      <div
        aria-hidden="true"
        className="absolute -right-1.5 top-1.5 h-full w-full rounded border-2 opacity-50"
        style={{ borderColor: main, backgroundColor: background }}
      />
      <div className="relative grid min-h-20 place-items-center rounded border bg-white/55 px-2 py-3 text-center">
        <div className="text-[10px] font-black uppercase tracking-normal" style={{ color: main }}>
          {deck.title}
        </div>
        <svg aria-hidden="true" className="mt-1 h-10 w-16" viewBox="0 0 96 64">
          {isChance ? <QuestionMark color={main} accent={accent} /> : <Chest color={main} accent={accent} />}
        </svg>
      </div>
    </div>
  );
}
