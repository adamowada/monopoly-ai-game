import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { GamePlaySurface } from "./game-play-surface";
import type { GameMetadata } from "../lib/api/games";

const routerMock = vi.hoisted(() => ({
  push: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => routerMock,
}));

const createdAt = "2026-07-04T00:00:00.000Z";
const apiBaseUrl = "http://api.test";
const gameId = "game-turn-controls";
const adaId = "player-1";
const graceId = "player-2";
const linId = "player-3";

type FetchMock = ReturnType<typeof vi.fn<typeof fetch>>;
type EventSourceListener = (event: Event) => void;
let originalScrollIntoView: typeof HTMLElement.prototype.scrollIntoView | undefined;
let originalScrollHeightDescriptor: PropertyDescriptor | undefined;
let originalScrollTopDescriptor: PropertyDescriptor | undefined;

class FakeEventSource {
  static instances: FakeEventSource[] = [];

  readonly url: string;
  private readonly listeners = new Map<string, EventSourceListener[]>();

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: EventSourceListener) {
    this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
  }

  removeEventListener(type: string, listener: EventSourceListener) {
    this.listeners.set(
      type,
      (this.listeners.get(type) ?? []).filter((entry) => entry !== listener),
    );
  }

  close() {
    this.listeners.clear();
  }

  dispatch(type: string) {
    const event = new Event(type);
    for (const listener of this.listeners.get(type) ?? []) {
      listener(event);
    }
  }
}

function gameFixture(position = 0): GameMetadata {
  return {
    id: gameId,
    status: "active",
    ruleset_version: "classic-v1",
    seed: "turn-controls",
    current_phase: "START_TURN",
    settings: {
      player_colors: [
        { seat_order: 0, color: "#0f766e" },
        { seat_order: 1, color: "#7c3aed" },
      ],
    },
    created_at: createdAt,
    updated_at: createdAt,
    players: [
      {
        id: adaId,
        game_id: gameId,
        seat_order: 0,
        name: "Ada",
        controller_type: "human",
        status: "active",
        state: {
          cash: 1500,
          position,
        },
        created_at: createdAt,
        updated_at: createdAt,
      },
      {
        id: graceId,
        game_id: gameId,
        seat_order: 1,
        name: "Grace",
        controller_type: "ai",
        status: "active",
        state: {
          cash: 1500,
          position: 0,
        },
        created_at: createdAt,
        updated_at: createdAt,
      },
    ],
  };
}

function stateFixture(position = 0, eventSequence = 0) {
  return {
    game_id: gameId,
    state: {
      game_id: gameId,
      seed: "turn-controls",
      players: [
        { id: adaId, cash: 1500, position },
        { id: graceId, cash: 1500, position: 0 },
      ],
      turn: {
        phase: "START_TURN",
        current_player_index: 0,
        current_player_id: adaId,
      },
    },
    state_hash: `state-${eventSequence}`,
    event_sequence: eventSequence,
  };
}

function nextTurnStateFixture(position = 0, eventSequence = 0) {
  const base = stateFixture(position, eventSequence);
  return {
    ...base,
    state: {
      ...base.state,
      turn: {
        phase: "START_TURN",
        current_player_index: 1,
        current_player_id: graceId,
      },
    },
  };
}

function debtStateFixture(eventSequence = 0) {
  const state = stateFixture(4, eventSequence);
  return {
    ...state,
    state: {
      ...state.state,
      players: [
        { id: adaId, cash: 1500, position: 4 },
        { id: graceId, cash: 1500, position: 0 },
      ],
      turn: {
        phase: "PAYMENT_RESOLUTION",
        current_player_index: 0,
        current_player_id: adaId,
      },
      active_payment: {
        debtor_id: adaId,
        creditor_id: graceId,
        amount_owed: 6,
        amount_paid: 0,
        reason: "rent:property_oriental_avenue",
        negotiation_allowed: true,
      },
    },
    state_hash: `debt-state-${eventSequence}`,
  };
}

function holdingsStateFixture() {
  const state = stateFixture(0, 4);
  return {
    ...state,
    state: {
      ...state.state,
      property_ownership: [
        {
          property_id: "property_oriental_avenue",
          owner_id: adaId,
          mortgaged: false,
          houses: 0,
          hotels: 0,
          hotel: false,
        },
        {
          property_id: "property_park_place",
          owner_id: graceId,
          mortgaged: false,
          houses: 0,
          hotels: 0,
          hotel: false,
        },
      ],
    },
  };
}

function aiStateFixture(eventSequence = 0) {
  return {
    game_id: gameId,
    state: {
      game_id: gameId,
      seed: "turn-controls-ai",
      players: [
        { id: adaId, cash: 1500, position: 0 },
        { id: graceId, cash: 1500, position: 0 },
      ],
      turn: {
        phase: "START_TURN",
        current_player_index: 1,
        current_player_id: graceId,
      },
    },
    state_hash: `ai-state-${eventSequence}`,
    event_sequence: eventSequence,
  };
}

function aiPostRollStateFixture(eventSequence = 2) {
  const base = aiStateFixture(eventSequence);
  return {
    ...base,
    state: {
      ...base.state,
      players: [
        { id: adaId, cash: 1500, position: 0 },
        { id: graceId, cash: 1500, position: 5 },
      ],
      turn: {
        phase: "POST_ROLL_MANAGEMENT",
        current_player_index: 1,
        current_player_id: graceId,
      },
    },
    state_hash: `ai-state-${eventSequence}`,
    event_sequence: eventSequence,
  };
}

function aiNearMonopolyStateFixture(eventSequence = 0) {
  const base = stateFixture(0, eventSequence);
  return {
    ...base,
    state: {
      ...base.state,
      players: [
        { id: adaId, cash: 1500, position: 0 },
        { id: graceId, cash: 1500, position: 0 },
      ],
      turn: {
        phase: "START_TURN",
        current_player_index: 0,
        current_player_id: adaId,
      },
      property_ownership: [
        {
          property_id: "property_st_james_place",
          owner_id: adaId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_tennessee_avenue",
          owner_id: graceId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_new_york_avenue",
          owner_id: adaId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
      ],
    },
    state_hash: `ai-near-monopoly-state-${eventSequence}`,
    event_sequence: eventSequence,
  };
}

function aiNearRailroadSetStateFixture(eventSequence = 0) {
  const base = stateFixture(0, eventSequence);
  return {
    ...base,
    state: {
      ...base.state,
      players: [
        { id: adaId, cash: 1500, position: 0 },
        { id: graceId, cash: 1500, position: 0 },
      ],
      turn: {
        phase: "START_TURN",
        current_player_index: 0,
        current_player_id: adaId,
      },
      property_ownership: [
        {
          property_id: "property_reading_railroad",
          owner_id: adaId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_pennsylvania_railroad",
          owner_id: adaId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_b_and_o_railroad",
          owner_id: adaId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_short_line_railroad",
          owner_id: graceId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
      ],
    },
    state_hash: `ai-near-railroad-state-${eventSequence}`,
    event_sequence: eventSequence,
  };
}

function aiMultipleNearMonopoliesStateFixture(eventSequence = 0) {
  const base = stateFixture(0, eventSequence);
  return {
    ...base,
    state: {
      ...base.state,
      players: [
        { id: adaId, cash: 1500, position: 0 },
        { id: graceId, cash: 1500, position: 0 },
      ],
      turn: {
        phase: "START_TURN",
        current_player_index: 0,
        current_player_id: adaId,
      },
      property_ownership: [
        {
          property_id: "property_oriental_avenue",
          owner_id: adaId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_vermont_avenue",
          owner_id: adaId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_connecticut_avenue",
          owner_id: graceId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_st_james_place",
          owner_id: adaId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_tennessee_avenue",
          owner_id: graceId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_new_york_avenue",
          owner_id: adaId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
      ],
    },
    state_hash: `ai-multiple-near-monopoly-state-${eventSequence}`,
    event_sequence: eventSequence,
  };
}

function aiBlockOpponentNearMonopolyStateFixture(eventSequence = 0) {
  const base = stateFixture(0, eventSequence);
  return {
    ...base,
    state: {
      ...base.state,
      players: [
        { id: adaId, cash: 1500, position: 0 },
        { id: graceId, cash: 1500, position: 0 },
        { id: linId, cash: 1500, position: 0 },
      ],
      turn: {
        phase: "START_TURN",
        current_player_index: 0,
        current_player_id: adaId,
      },
      property_ownership: [
        {
          property_id: "property_st_james_place",
          owner_id: graceId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_tennessee_avenue",
          owner_id: linId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_new_york_avenue",
          owner_id: graceId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
      ],
    },
    state_hash: `ai-block-orange-state-${eventSequence}`,
    event_sequence: eventSequence,
  };
}

function aiCompletionAndHigherValueBlockStateFixture(eventSequence = 0) {
  const base = stateFixture(0, eventSequence);
  return {
    ...base,
    state: {
      ...base.state,
      players: [
        { id: adaId, cash: 1500, position: 0 },
        { id: graceId, cash: 1500, position: 0 },
        { id: linId, cash: 1500, position: 0 },
      ],
      turn: {
        phase: "START_TURN",
        current_player_index: 0,
        current_player_id: adaId,
      },
      property_ownership: [
        {
          property_id: "property_mediterranean_avenue",
          owner_id: adaId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_baltic_avenue",
          owner_id: linId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_st_james_place",
          owner_id: graceId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_tennessee_avenue",
          owner_id: linId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
        {
          property_id: "property_new_york_avenue",
          owner_id: graceId,
          mortgaged: false,
          houses: 0,
          hotel: false,
        },
      ],
    },
    state_hash: `ai-completion-and-block-state-${eventSequence}`,
    event_sequence: eventSequence,
  };
}

function metadataFallbackAiGame(): GameMetadata {
  const game = gameFixture();
  return {
    ...game,
    players: [
      {
        ...game.players[0],
        name: "Ada AI",
        controller_type: "ai",
      },
      {
        ...game.players[1],
        name: "Grace AI",
        controller_type: "ai",
      },
    ],
  };
}

function metadataThreeAiGame(): GameMetadata {
  const game = metadataFallbackAiGame();
  return {
    ...game,
    players: [
      ...game.players,
      {
        id: linId,
        game_id: gameId,
        seat_order: 2,
        name: "Lin AI",
        controller_type: "ai",
        status: "active",
        state: {
          cash: 1500,
          position: 0,
        },
        created_at: createdAt,
        updated_at: createdAt,
      },
    ],
  };
}

function legalAction(
  type: string,
  payload: Record<string, unknown> = {},
  expectedStateHash = "state-0",
  expectedEventSequence = 0,
  description: string | null = null,
) {
  return {
    actor_id: adaId,
    type,
    payload,
    expected_state_hash: expectedStateHash,
    expected_event_sequence: expectedEventSequence,
    description,
    schema: {},
  };
}

function aiLegalAction(
  type: string,
  payload: Record<string, unknown> = {},
  expectedStateHash = "ai-state-0",
  expectedEventSequence = 0,
) {
  return {
    ...legalAction(type, payload, expectedStateHash, expectedEventSequence),
    actor_id: graceId,
  };
}

function eventsFixture(events: Array<Record<string, unknown>> = []) {
  return { events };
}

function rejectedActionsFixture(records: Array<Record<string, unknown>> = []) {
  return { rejected_actions: records };
}

const aiAuditResponses = [
  { path: "/ai/profiles", payload: { profiles: [] } },
  { path: "/ai/decisions", payload: { decisions: [] } },
  { path: "/ai/self-dialogue", payload: { self_dialogue: [] } },
  { path: "/ai/memory", payload: { memory_entries: [] } },
  { path: "/ai/retrieval-records", payload: { retrieval_records: [] } },
  { path: "/ai/rejected-outputs", payload: { rejected_outputs: [] } },
];

function aiAuditUrl(path: string): string {
  return `${apiBaseUrl}/games/${gameId}${path}`;
}

function aiAuditFetchCount(fetchMock: FetchMock, path: string): number {
  const url = aiAuditUrl(path);
  return fetchMock.mock.calls.filter(([input]) => String(input) === url).length;
}

function acceptedRollResponse() {
  return {
    status: "accepted",
    game_id: gameId,
    accepted_events: [
      {
        id: "event-1",
        game_id: gameId,
        sequence: 1,
        actor_player_id: adaId,
        event_type: "DICE_ROLLED",
        payload: { dice: [3, 4], total: 7 },
        state_hash: "state-1",
        created_at: "2026-07-04T00:01:00.000Z",
      },
      {
        id: "event-2",
        game_id: gameId,
        sequence: 2,
        actor_player_id: adaId,
        event_type: "PLAYER_POSITION_SET",
        payload: { player_id: adaId, position: 7 },
        state_hash: "state-2",
        created_at: "2026-07-04T00:01:01.000Z",
      },
    ],
    state: stateFixture(7, 2).state,
    state_hash: "state-2",
    event_sequence: 2,
  };
}

function acceptedBackendDiceRollResponse() {
  return {
    ...acceptedRollResponse(),
    accepted_events: [
      {
        id: "event-1",
        game_id: gameId,
        sequence: 1,
        actor_player_id: adaId,
        event_type: "DICE_ROLLED",
        payload: { player_id: adaId, die_1: 3, die_2: 4, total: 7, is_doubles: false, roll_counter: 1 },
        state_hash: "state-1",
        created_at: "2026-07-04T00:01:00.000Z",
      },
      {
        id: "event-2",
        game_id: gameId,
        sequence: 2,
        actor_player_id: adaId,
        event_type: "PLAYER_POSITION_SET",
        payload: { player_id: adaId, position: 7 },
        state_hash: "state-2",
        created_at: "2026-07-04T00:01:01.000Z",
      },
    ],
  };
}

function acceptedReadingRailroadRollResponse() {
  return {
    ...acceptedRollResponse(),
    accepted_events: [
      {
        id: "event-short-1",
        game_id: gameId,
        sequence: 1,
        actor_player_id: adaId,
        event_type: "DICE_ROLLED",
        payload: { player_id: adaId, die_1: 2, die_2: 3, total: 5, is_doubles: false, roll_counter: 1 },
        state_hash: "state-1",
        created_at: "2026-07-04T00:01:00.000Z",
      },
      {
        id: "event-short-2",
        game_id: gameId,
        sequence: 2,
        actor_player_id: adaId,
        event_type: "PLAYER_POSITION_SET",
        payload: { player_id: adaId, position: 5 },
        state_hash: "state-2",
        created_at: "2026-07-04T00:01:01.000Z",
      },
    ],
    state: stateFixture(5, 2).state,
    state_hash: "state-2",
    event_sequence: 2,
  };
}

function acceptedJailRollResponse() {
  return {
    ...acceptedRollResponse(),
    accepted_events: [
      {
        id: "event-jail-1",
        game_id: gameId,
        sequence: 1,
        actor_player_id: adaId,
        event_type: "DICE_ROLLED",
        payload: { player_id: adaId, die_1: 2, die_2: 4, total: 6, is_doubles: false, roll_counter: 1 },
        state_hash: "state-1",
        created_at: "2026-07-04T00:01:00.000Z",
      },
      {
        id: "event-jail-2",
        game_id: gameId,
        sequence: 2,
        actor_player_id: adaId,
        event_type: "PLAYER_POSITION_SET",
        payload: { player_id: adaId, position: 30 },
        state_hash: "state-2",
        created_at: "2026-07-04T00:01:01.000Z",
      },
      {
        id: "event-jail-3",
        game_id: gameId,
        sequence: 3,
        actor_player_id: adaId,
        event_type: "PLAYER_POSITION_SET",
        payload: { player_id: adaId, position: 10 },
        state_hash: "state-3",
        created_at: "2026-07-04T00:01:02.000Z",
      },
      {
        id: "event-jail-4",
        game_id: gameId,
        sequence: 4,
        actor_player_id: adaId,
        event_type: "PLAYER_JAIL_SET",
        payload: { player_id: adaId, in_jail: true, jail_turns: 0 },
        state_hash: "state-4",
        created_at: "2026-07-04T00:01:03.000Z",
      },
      {
        id: "event-jail-5",
        game_id: gameId,
        sequence: 5,
        actor_player_id: adaId,
        event_type: "TURN_STATE_SET",
        payload: {
          phase: "POST_ROLL_MANAGEMENT",
          turn_number: 1,
          current_player_id: adaId,
          consecutive_doubles: 0,
          current_player_index: 0,
        },
        state_hash: "state-5",
        created_at: "2026-07-04T00:01:04.000Z",
      },
    ],
    state: {
      ...stateFixture(10, 5).state,
      players: [
        { id: adaId, cash: 1500, position: 10, in_jail: true },
        { id: graceId, cash: 1500, position: 0 },
      ],
      turn: {
        phase: "POST_ROLL_MANAGEMENT",
        current_player_index: 0,
        current_player_id: adaId,
      },
    },
    state_hash: "state-5",
    event_sequence: 5,
  };
}

function acceptedEndTurnResponse() {
  const nextState = nextTurnStateFixture(5, 3);
  return {
    status: "accepted",
    game_id: gameId,
    accepted_events: [
      {
        id: "event-end-turn-3",
        game_id: gameId,
        sequence: 3,
        actor_player_id: adaId,
        event_type: "TURN_ENDED",
        payload: { player_id: adaId, next_player_id: graceId },
        state_hash: "state-3",
        created_at: "2026-07-04T00:01:02.000Z",
      },
    ],
    state: nextState.state,
    state_hash: nextState.state_hash,
    event_sequence: nextState.event_sequence,
  };
}

function acceptedAiDiceStepResponse() {
  return aiStepResponse("done", {
    accepted_events: [
      {
        id: "event-ai-1",
        game_id: gameId,
        sequence: 1,
        actor_player_id: graceId,
        event_type: "DICE_ROLLED",
        payload: { player_id: graceId, die_1: 5, die_2: 2, total: 7, is_doubles: false, roll_counter: 1 },
        state_hash: "ai-state-1",
        created_at: "2026-07-04T00:01:00.000Z",
      },
      {
        id: "event-ai-2",
        game_id: gameId,
        sequence: 2,
        actor_player_id: graceId,
        event_type: "PLAYER_POSITION_SET",
        payload: { player_id: graceId, position: 7 },
        state_hash: "ai-state-2",
        created_at: "2026-07-04T00:01:01.000Z",
      },
    ],
    accepted_event_id: "event-ai-2",
  });
}

function acceptedAiReadingRailroadStepResponse() {
  return aiStepResponse("done", {
    accepted_events: [
      {
        id: "event-ai-short-1",
        game_id: gameId,
        sequence: 1,
        actor_player_id: graceId,
        event_type: "DICE_ROLLED",
        payload: { player_id: graceId, die_1: 2, die_2: 3, total: 5, is_doubles: false, roll_counter: 1 },
        state_hash: "ai-state-1",
        created_at: "2026-07-04T00:01:00.000Z",
      },
      {
        id: "event-ai-short-2",
        game_id: gameId,
        sequence: 2,
        actor_player_id: graceId,
        event_type: "PLAYER_POSITION_SET",
        payload: { player_id: graceId, position: 5 },
        state_hash: "ai-state-2",
        created_at: "2026-07-04T00:01:01.000Z",
      },
    ],
    accepted_event_id: "event-ai-short-2",
  });
}

function acceptedAiPropertyFollowUpStepResponse() {
  return aiStepResponse("done", {
    accepted_events: [
      {
        id: "event-ai-buy-3",
        game_id: gameId,
        sequence: 3,
        actor_player_id: graceId,
        event_type: "PROPERTY_OWNER_SET",
        payload: { owner_id: graceId, property_id: "property_reading_railroad" },
        state_hash: "ai-state-3",
        created_at: "2026-07-04T00:01:02.000Z",
      },
    ],
    accepted_event_id: "event-ai-buy-3",
  });
}

function acceptedDebtSettlementResponse() {
  const settledState = debtStateFixture(4);
  return {
    status: "accepted",
    game_id: gameId,
    accepted_events: [
      {
        id: "event-debt-1",
        game_id: gameId,
        sequence: 1,
        actor_player_id: adaId,
        event_type: "PLAYER_CASH_DELTA",
        payload: { player_id: adaId, amount: -6 },
        state_hash: "debt-state-1",
        created_at: "2026-07-04T00:01:00.000Z",
      },
      {
        id: "event-debt-2",
        game_id: gameId,
        sequence: 2,
        actor_player_id: adaId,
        event_type: "PLAYER_CASH_DELTA",
        payload: { player_id: graceId, amount: 6 },
        state_hash: "debt-state-2",
        created_at: "2026-07-04T00:01:01.000Z",
      },
      {
        id: "event-debt-3",
        game_id: gameId,
        sequence: 3,
        actor_player_id: adaId,
        event_type: "ACTIVE_PAYMENT_SET",
        payload: { active: false },
        state_hash: "debt-state-3",
        created_at: "2026-07-04T00:01:02.000Z",
      },
      {
        id: "event-debt-4",
        game_id: gameId,
        sequence: 4,
        actor_player_id: adaId,
        event_type: "TURN_STATE_SET",
        payload: {
          turn_number: 1,
          current_player_index: 0,
          current_player_id: adaId,
          phase: "POST_ROLL_MANAGEMENT",
          consecutive_doubles: 0,
        },
        state_hash: "debt-state-4",
        created_at: "2026-07-04T00:01:03.000Z",
      },
    ],
    state: {
      ...settledState.state,
      players: [
        { id: adaId, cash: 1494, position: 4 },
        { id: graceId, cash: 1506, position: 0 },
      ],
      turn: {
        phase: "POST_ROLL_MANAGEMENT",
        current_player_index: 0,
        current_player_id: adaId,
      },
      active_payment: null,
    },
    state_hash: "debt-state-4",
    event_sequence: 4,
  };
}

function acceptedChanceCardResponse() {
  const response = acceptedRollResponse();
  return {
    ...response,
    accepted_events: [
      ...response.accepted_events,
      {
        id: "event-3",
        game_id: gameId,
        sequence: 3,
        actor_player_id: adaId,
        event_type: "CARD_DRAWN",
        payload: {
          deck: "chance",
          card_id: "card_chance_advance_to_go",
          draw_counter: 1,
        },
        state_hash: "state-3",
        created_at: "2026-07-04T00:01:02.000Z",
      },
    ],
    event_sequence: 3,
    state_hash: "state-3",
  };
}

function auctionPurchaseStateFixture() {
  return {
    game_id: gameId,
    state: {
      game_id: gameId,
      seed: "turn-controls-auction",
      players: [
        { id: adaId, cash: 1500, position: 1 },
        { id: graceId, cash: 1500, position: 0 },
      ],
      property_ownership: [
        {
          property_id: "property_mediterranean_avenue",
          owner_id: null,
          mortgaged: false,
          houses: 0,
          hotels: 0,
          hotel: false,
        },
      ],
      active_auction: null,
      turn: {
        phase: "PURCHASE_OR_AUCTION",
        current_player_index: 0,
        current_player_id: adaId,
      },
    },
    state_hash: "state-2",
    event_sequence: 2,
  };
}

function mixedAuctionAiTurnStateFixture(eventSequence = 8) {
  return {
    game_id: gameId,
    state: {
      game_id: gameId,
      seed: "turn-controls-mixed-auction",
      players: [
        { id: adaId, cash: 1500, position: 1 },
        { id: graceId, cash: 1500, position: 0 },
      ],
      property_ownership: [
        {
          property_id: "property_mediterranean_avenue",
          owner_id: null,
          mortgaged: false,
          houses: 0,
          hotels: 0,
          hotel: false,
        },
      ],
      active_auction: {
        property_id: "property_mediterranean_avenue",
        high_bidder_id: null,
        high_bid_amount: null,
        passed_player_ids: [],
      },
      turn: {
        phase: "AUCTION",
        current_player_index: 1,
        current_player_id: graceId,
      },
    },
    state_hash: `auction-ai-state-${eventSequence}`,
    event_sequence: eventSequence,
  };
}

function mixedAuctionHumanTurnStateFixture(eventSequence = 8) {
  const auctionState = mixedAuctionAiTurnStateFixture(eventSequence);
  return {
    ...auctionState,
    state: {
      ...auctionState.state,
      turn: {
        phase: "AUCTION",
        current_player_index: 0,
        current_player_id: adaId,
      },
    },
    state_hash: `auction-human-state-${eventSequence}`,
  };
}

function auctionHighBidderAiTurnStateFixture(eventSequence = 9) {
  const auctionState = mixedAuctionHumanTurnStateFixture(eventSequence);
  return {
    ...auctionState,
    state: {
      ...auctionState.state,
      active_auction: {
        property_id: "property_mediterranean_avenue",
        high_bidder_id: adaId,
        high_bid_amount: 26,
        passed_player_ids: [],
      },
      turn: {
        phase: "AUCTION",
        current_player_index: 0,
        current_player_id: adaId,
      },
    },
    state_hash: `auction-high-bidder-ai-state-${eventSequence}`,
  };
}

function acceptedAuctionRollResponse() {
  return {
    ...acceptedRollResponse(),
    accepted_events: [
      {
        id: "event-1",
        game_id: gameId,
        sequence: 1,
        actor_player_id: adaId,
        event_type: "DICE_ROLLED",
        payload: { dice: [1], total: 1 },
        state_hash: "state-1",
        created_at: "2026-07-04T00:01:00.000Z",
      },
      {
        id: "event-2",
        game_id: gameId,
        sequence: 2,
        actor_player_id: adaId,
        event_type: "PLAYER_POSITION_SET",
        payload: { player_id: adaId, position: 1 },
        state_hash: "state-2",
        created_at: "2026-07-04T00:01:01.000Z",
      },
    ],
    state: auctionPurchaseStateFixture().state,
    state_hash: "state-2",
    event_sequence: 2,
  };
}

function rejectedRollResponse() {
  return {
    status: "rejected",
    rejected_action_id: "rejection-1",
    reason_code: "stale_action",
    validation_errors: [
      {
        code: "stale_action",
        message: "action expected state no longer matches current state",
        field: "expected_state_hash",
      },
    ],
    legal_action_context: {
      legal_actions: ["ROLL_DICE"],
    },
    submitted_action: legalAction("ROLL_DICE"),
  };
}

function aiStepResponse(status: "accepted" | "rejected" | "blocked" | "done", patch: Record<string, unknown> = {}) {
  return {
    status,
    game_id: gameId,
    player_id: graceId,
    decision_type: "action_decision",
    negotiation_id: null,
    ai_decision_id: `ai-decision-${status}`,
    accepted_events: [],
    accepted_event_id: null,
    rejected_action_id: status === "rejected" || status === "blocked" ? `rejected-${status}` : null,
    game_status: status === "blocked" ? "AI_BLOCKED" : "active",
    consumed_response_opportunity: false,
    consumed_negotiation_opportunity: null,
    outcome: { kind: status === "accepted" ? "action_decision" : `ai_${status}`, status },
    reason_code: status === "rejected" ? "illegal_action" : status === "blocked" ? "codex_exec_timeout" : null,
    validation_errors:
      status === "rejected" || status === "blocked"
        ? [
            {
              code: status === "blocked" ? "codex_exec_timeout" : "illegal_action",
              message: status === "blocked" ? "codex exec timed out" : "action is not legal",
              field: null,
            },
          ]
        : [],
    ...patch,
  };
}

function renderSurface(fetchMock: FetchMock, initialGame: GameMetadata = gameFixture()) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Number.POSITIVE_INFINITY },
      mutations: { retry: false },
    },
  });

  vi.stubGlobal("fetch", fetchMock);

  return render(
    <QueryClientProvider client={queryClient}>
      <GamePlaySurface apiBaseUrl={apiBaseUrl} gameId={gameId} initialGame={initialGame} />
    </QueryClientProvider>,
  );
}

function jsonResponse(payload: unknown, init?: ResponseInit) {
  return Promise.resolve(Response.json(payload, init));
}

function baseFetchMock({
  game = gameFixture(),
  state = stateFixture(),
  legalActions = [legalAction("ROLL_DICE")],
  events = eventsFixture(),
  rejectedActions = rejectedActionsFixture(),
  actionResponse,
  endResponse,
}: {
  game?: GameMetadata;
  state?: ReturnType<typeof stateFixture>;
  legalActions?: Array<ReturnType<typeof legalAction>>;
  events?: ReturnType<typeof eventsFixture>;
  rejectedActions?: ReturnType<typeof rejectedActionsFixture>;
  actionResponse?: unknown;
  endResponse?: GameMetadata;
} = {}) {
  return vi.fn<typeof fetch>(async (input, init) => {
    const url = String(input);
    if (url === `${apiBaseUrl}/games/${gameId}`) {
      return Response.json(game);
    }
    if (url === `${apiBaseUrl}/games/${gameId}/state`) {
      return Response.json(state);
    }
    if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
      return Response.json({
        game_id: gameId,
        actor_player_id: adaId,
        legal_actions: legalActions,
        state_hash: state.state_hash,
        event_sequence: state.event_sequence,
      });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/events`) {
      return Response.json(events);
    }
    if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
      return Response.json(rejectedActions);
    }
    if (url === `${apiBaseUrl}/games/${gameId}/negotiations`) {
      return Response.json({ negotiations: [] });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/deals`) {
      return Response.json({ deals: [] });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/contracts`) {
      return Response.json({ contracts: [] });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/obligations`) {
      return Response.json({ obligations: [] });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/contracts/outcomes`) {
      return Response.json({ outcomes: [] });
    }
    const aiAuditResponse = aiAuditResponses.find((response) => url === aiAuditUrl(response.path));
    if (aiAuditResponse) {
      return Response.json(aiAuditResponse.payload);
    }
    if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST" && actionResponse) {
      return Response.json(actionResponse, {
        status: typeof actionResponse === "object" && actionResponse !== null && "reason_code" in actionResponse ? 409 : 200,
      });
    }
    if (url === `${apiBaseUrl}/games/${gameId}/end` && init?.method === "POST" && endResponse) {
      return Response.json(endResponse);
    }
    throw new Error(`Unexpected fetch ${url}`);
  });
}

afterEach(() => {
  vi.useRealTimers();
  if (originalScrollIntoView) {
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: originalScrollIntoView,
    });
  } else {
    delete (HTMLElement.prototype as Partial<HTMLElement>).scrollIntoView;
  }
  originalScrollIntoView = undefined;
  if (originalScrollHeightDescriptor) {
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", originalScrollHeightDescriptor);
  } else {
    Reflect.deleteProperty(HTMLElement.prototype, "scrollHeight");
  }
  if (originalScrollTopDescriptor) {
    Object.defineProperty(HTMLElement.prototype, "scrollTop", originalScrollTopDescriptor);
  } else {
    Reflect.deleteProperty(HTMLElement.prototype, "scrollTop");
  }
  originalScrollHeightDescriptor = undefined;
  originalScrollTopDescriptor = undefined;
  FakeEventSource.instances = [];
  window.localStorage.clear();
  routerMock.push.mockReset();
  vi.unstubAllGlobals();
});

describe("GamePlaySurface turn controls", () => {
  it("saves, loads, and ends the current game session", async () => {
    vi.stubGlobal("confirm", vi.fn(() => true));
    const endedGame = {
      ...gameFixture(),
      status: "ended",
      current_phase: "ENDED",
    };
    const fetchMock = baseFetchMock({ endResponse: endedGame });

    renderSurface(fetchMock);

    expect(screen.queryByRole("region", { name: "Game session" })).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Open game menu" }));
    const menu = screen.getByRole("menu", { name: "Game menu" });

    expect(within(menu).getByRole("menuitem", { name: "Board" })).toHaveAttribute("href", "#game-board");
    expect(within(menu).getByRole("menuitem", { name: "Current turn" })).toHaveAttribute("href", "#current-turn");
    expect(within(menu).getByRole("menuitem", { name: "Player trays" })).toHaveAttribute("href", "#player-trays");
    expect(within(menu).getByRole("menuitem", { name: "Properties" })).toHaveAttribute("href", "#properties");
    expect(within(menu).getByRole("menuitem", { name: "Deals" })).toHaveAttribute("href", "#deals");
    expect(within(menu).getByRole("menuitem", { name: "Contracts" })).toHaveAttribute("href", "#contracts");
    expect(within(menu).getByRole("menuitem", { name: "AI notebook" })).toHaveAttribute("href", "#ai-notebook");
    expect(within(menu).getByRole("menuitem", { name: "Game log" })).toHaveAttribute("href", "#game-log");

    const saveButton = within(menu).getByRole("menuitem", { name: "Save game" });
    const loadButton = within(menu).getByRole("menuitem", { name: "Load game" });
    expect(saveButton).toHaveAttribute("data-button-variant", "secondary");
    expect(saveButton).toHaveClass("text-neutral-800");
    expect(saveButton).not.toHaveClass("text-white");
    expect(loadButton).toHaveAttribute("data-button-variant", "secondary");
    expect(loadButton).toHaveClass("text-neutral-800");
    expect(loadButton).not.toHaveClass("text-white");

    fireEvent.click(saveButton);

    expect(window.localStorage.getItem("monopoly-ai-game.saved-games")).toContain(gameId);
    expect(menu).toHaveTextContent("Saved game-turn-controls");

    fireEvent.click(loadButton);
    fireEvent.click(await within(menu).findByRole("menuitem", { name: "Open game-turn-controls" }));
    expect(routerMock.push).toHaveBeenCalledWith("/games/game-turn-controls");

    fireEvent.click(within(menu).getByRole("menuitem", { name: "End game" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/end` && init?.method === "POST",
        ),
      ).toBe(true),
    );
    expect(routerMock.push).toHaveBeenCalledWith("/");
  });

  it("keeps a running log beside the board and secondary systems below the player trays", async () => {
    renderSurface(baseFetchMock());

    const layout = await screen.findByTestId("game-table-layout");
    expect(layout).toHaveClass("xl:grid-cols-[minmax(520px,640px)_minmax(0,1fr)]");

    const views = await screen.findByRole("tablist", { name: "Table views" });
    expect(within(views).queryByRole("tab", { name: "Game log" })).not.toBeInTheDocument();
    expect(within(views).getByRole("tab", { name: "Properties" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByTestId("running-log-panel")).toContainElement(screen.getByRole("region", { name: "Game log" }));
    expect(screen.getByTestId("secondary-table-panel")).toContainElement(views);
    expect(await screen.findByRole("region", { name: "Property management" })).toBeVisible();
    expect(screen.queryByRole("region", { name: "Negotiation inbox" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Contracts obligations panel" })).not.toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "AI audit" })).not.toBeInTheDocument();

    fireEvent.click(within(views).getByRole("tab", { name: "Deals" }));
    expect(await screen.findByRole("region", { name: "Negotiation inbox" })).toBeVisible();
    expect(screen.queryByRole("region", { name: "Property management" })).not.toBeInTheDocument();

    fireEvent.click(within(views).getByRole("tab", { name: "Contracts" }));
    expect(await screen.findByRole("region", { name: "Contracts obligations panel" })).toBeVisible();
    expect(screen.getByRole("region", { name: "Game log" })).toBeVisible();

    fireEvent.click(within(views).getByRole("tab", { name: "AI notebook" }));
    expect(await screen.findByRole("region", { name: "AI audit" })).toBeVisible();
  });

  it("does not pair unrelated bank payments with nearby cash receipts in the running log", async () => {
    renderSurface(
      baseFetchMock({
        events: eventsFixture([
          {
            id: "event-bank-payment",
            game_id: gameId,
            sequence: 1,
            actor_player_id: adaId,
            event_type: "PLAYER_CASH_DELTA",
            payload: { player_id: adaId, amount: -200 },
            state_hash: "state-1",
            created_at: "2026-07-04T00:01:00.000Z",
          },
          {
            id: "event-deed",
            game_id: gameId,
            sequence: 2,
            actor_player_id: adaId,
            event_type: "PROPERTY_OWNER_SET",
            payload: { owner_id: adaId, property_id: "property_pennsylvania_railroad" },
            state_hash: "state-2",
            created_at: "2026-07-04T00:01:01.000Z",
          },
          {
            id: "event-go-salary",
            game_id: gameId,
            sequence: 3,
            actor_player_id: graceId,
            event_type: "PLAYER_CASH_DELTA",
            payload: { player_id: graceId, amount: 200 },
            state_hash: "state-3",
            created_at: "2026-07-04T00:01:02.000Z",
          },
        ]),
      }),
    );

    const log = await screen.findByRole("region", { name: "Game log" });

    await waitFor(() => expect(log).toHaveTextContent("Ada paid $200."));
    expect(log).toHaveTextContent("Grace received $200.");
    expect(log).not.toHaveTextContent("Ada paid Grace $200.");
  });

  it("renders each paired player cash transfer once in the running log", async () => {
    renderSurface(
      baseFetchMock({
        events: eventsFixture([
          {
            id: "event-rent-payment",
            game_id: gameId,
            sequence: 1,
            actor_player_id: adaId,
            event_type: "PLAYER_CASH_DELTA",
            payload: { player_id: adaId, amount: -22 },
            state_hash: "state-1",
            created_at: "2026-07-04T00:01:00.000Z",
          },
          {
            id: "event-rent-receipt",
            game_id: gameId,
            sequence: 2,
            actor_player_id: adaId,
            event_type: "PLAYER_CASH_DELTA",
            payload: { player_id: graceId, amount: 22 },
            state_hash: "state-2",
            created_at: "2026-07-04T00:01:01.000Z",
          },
        ]),
      }),
    );

    const log = await screen.findByRole("region", { name: "Game log" });

    await waitFor(() => expect(within(log).getAllByText("Ada paid Grace $22.")).toHaveLength(1));
  });

  it("prioritizes active controls, current player holdings, and one dynamic turn context", async () => {
    const fetchMock = baseFetchMock({
      state: holdingsStateFixture(),
      events: eventsFixture([
        {
          id: "event-4",
          game_id: gameId,
          sequence: 4,
          actor_player_id: adaId,
          event_type: "PROPERTY_OWNER_SET",
          payload: {
            owner_id: adaId,
            property_id: "property_oriental_avenue",
          },
          state_hash: "state-4",
          created_at: "2026-07-04T00:04:00.000Z",
        },
      ]),
    });

    renderSurface(fetchMock);

    expect(await screen.findByRole("region", { name: "Active player" })).toBeInTheDocument();
    expect(await screen.findByRole("region", { name: "Turn controls" })).toBeInTheDocument();

    const trays = await screen.findByRole("region", { name: "Player trays" });
    expect(within(trays).queryByText("Player trays")).not.toBeInTheDocument();
    expect(within(trays).queryByText("Switch seats without shrinking every player into a tiny card.")).not.toBeInTheDocument();
    expect(trays).not.toHaveTextContent(/\b\d+\s+seats\b/i);
    const trayTabs = within(trays).getByRole("tablist", { name: "Player tray tabs" });
    expect(trayTabs).toHaveClass("flex-wrap");
    expect(trayTabs).not.toHaveClass("overflow-x-auto");
    const adaTray = within(trays).getByRole("tabpanel", { name: "Ada active player tray current turn" });
    expect(adaTray).toHaveAttribute("data-current-player", "true");
    expect(adaTray).toHaveTextContent("$1,500");
    expect(adaTray).toHaveTextContent("GO");
    expect(adaTray).toHaveTextContent("Oriental Avenue");
    expect(adaTray).not.toHaveTextContent("Park Place");
    expect(adaTray).not.toHaveTextContent("No current contracts or obligations.");
    expect(adaTray).not.toHaveTextContent("No deeds yet.");

    const graceTab = within(trays).getByRole("tab", { name: /Grace/ });
    expect(graceTab).toHaveClass("rounded-t-md");
    fireEvent.click(graceTab);
    const graceTray = within(trays).getByRole("tabpanel", { name: "Grace active player tray" });
    expect(graceTray).toHaveTextContent("$1,500");
    expect(graceTray).toHaveTextContent("Park Place");
    expect(graceTray).not.toHaveTextContent("No current contracts or obligations.");
    expect(graceTray).not.toHaveTextContent("No deeds yet.");

    const context = await screen.findByRole("region", { name: "Turn context" });
    expect(context).toHaveTextContent("Last turn result");
    expect(context).toHaveTextContent("Ada owns Oriental Avenue");
  });

  it("renders enabled action buttons only for backend-returned legal actions", async () => {
    renderSurface(
      baseFetchMock({
        legalActions: [
          legalAction("ROLL_DICE"),
          legalAction("DECLARE_BANKRUPTCY"),
          legalAction("PAY_JAIL_FINE", { amount: 50 }, "state-0", 0, "Pay $50 to leave jail before rolling."),
        ],
      }),
    );

    const controls = await screen.findByRole("region", { name: "Turn controls" });

    expect(await within(controls).findByRole("button", { name: "Roll dice" })).toBeEnabled();
    expect(within(controls).getByRole("button", { name: "Pay jail fine" })).toBeEnabled();
    expect(controls).toHaveTextContent("Pay $50 to leave jail before rolling.");
    expect(within(controls).getByRole("button", { name: "End turn" })).toBeDisabled();
    expect(within(controls).queryByRole("button", { name: "Buy property" })).not.toBeInTheDocument();
    expect(within(controls).queryByRole("button", { name: "Start auction" })).not.toBeInTheDocument();
    expect(within(controls).queryByRole("button", { name: "Bid auction" })).not.toBeInTheDocument();
    expect(within(controls).queryByRole("button", { name: "Pass auction" })).not.toBeInTheDocument();
    expect(within(controls).queryByRole("button", { name: "Settle debt" })).not.toBeInTheDocument();
    expect(within(controls).queryByRole("button", { name: "Declare bankruptcy" })).not.toBeInTheDocument();
  });

  it("keeps voluntary bankruptcy in the game menu behind confirmation", async () => {
    const fetchMock = baseFetchMock({
      legalActions: [legalAction("ROLL_DICE")],
      actionResponse: {
        ...acceptedRollResponse(),
        submitted_action: legalAction("DECLARE_BANKRUPTCY", { creditor_id: null }),
      },
    });

    renderSurface(fetchMock);

    const controls = await screen.findByRole("region", { name: "Turn controls" });
    expect(within(controls).queryByRole("button", { name: "Declare bankruptcy" })).not.toBeInTheDocument();

    fireEvent.click(await screen.findByRole("button", { name: "Open game menu" }));
    const menu = screen.getByRole("menu", { name: "Game menu" });
    fireEvent.click(within(menu).getByRole("menuitem", { name: "Declare bankruptcy" }));

    const dialog = await screen.findByRole("dialog", { name: "Confirm bankruptcy" });
    expect(dialog).toHaveTextContent("Ada");
    expect(dialog).toHaveTextContent("give up and lose");

    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog", { name: "Confirm bankruptcy" })).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST",
      ),
    ).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: "Open game menu" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "Declare bankruptcy" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm bankruptcy" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST",
        ),
      ).toBe(true),
    );
    const submittedCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST",
    );
    expect(JSON.parse(String(submittedCall?.[1]?.body))).toMatchObject({
      actor_id: adaId,
      type: "DECLARE_BANKRUPTCY",
      payload: { creditor_id: null },
    });
  });

  it("settles an active debt with the backend-provided legal action payload", async () => {
    const state = debtStateFixture();
    const debtPayload = {
      amount: 6,
      debt_id: "active-debt:game-turn-controls:0:player-1:player-2:6:0:rent:property_oriental_avenue",
      creditor_player_id: graceId,
    };
    const fetchMock = baseFetchMock({
      game: gameFixture(4),
      state,
      legalActions: [legalAction("SETTLE_DEBT", debtPayload, state.state_hash, state.event_sequence)],
      actionResponse: acceptedDebtSettlementResponse(),
    });

    renderSurface(fetchMock, gameFixture(4));

    const controls = await screen.findByRole("region", { name: "Turn controls" });
    const payment = await within(controls).findByRole("status", { name: "Active payment" });
    expect(payment).toHaveTextContent("Ada owes Grace $6");
    expect(payment).toHaveTextContent("Rent for Oriental Avenue");
    fireEvent.click(await within(controls).findByRole("button", { name: "Settle debt" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST",
        ),
      ).toBe(true),
    );
    const submittedCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST",
    );
    expect(JSON.parse(String(submittedCall?.[1]?.body))).toEqual(
      expect.objectContaining({
        type: "SETTLE_DEBT",
        payload: debtPayload,
      }),
    );
    const context = await screen.findByRole("region", { name: "Turn context" });
    await waitFor(() => expect(context).toHaveTextContent("Ada paid Grace $6."));
    expect(screen.queryByRole("region", { name: "Rejected action" })).not.toBeInTheDocument();
  });

  it("updates the board, active player position, and Game log after an accepted action is refetched", async () => {
    let accepted = false;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(accepted ? gameFixture(7) : gameFixture(0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(accepted ? stateFixture(7, 2) : stateFixture(0, 0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: accepted ? [legalAction("BUY_PROPERTY", { property_id: "property_chance_7" })] : [legalAction("ROLL_DICE")],
          state_hash: accepted ? "state-2" : "state-0",
          event_sequence: accepted ? 2 : 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(accepted ? eventsFixture(acceptedRollResponse().accepted_events) : eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/contracts`) {
        return Response.json({ contracts: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/obligations`) {
        return Response.json({ obligations: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/contracts/outcomes`) {
        return Response.json({ outcomes: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/deals`) {
        return Response.json({ deals: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        accepted = true;
        return Response.json(acceptedRollResponse());
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    expect(await screen.findByLabelText("Ada token at GO, position 0")).toBeVisible();
    fireEvent.click(await screen.findByRole("button", { name: "Roll dice" }));

    expect(await screen.findByLabelText("Ada token at Chance, position 7", {}, { timeout: 12_000 })).toBeVisible();
    expect(screen.getByRole("status", { name: "Dice roll animation" })).toHaveTextContent("3 + 4");
    const activePlayer = screen.getByRole("region", { name: "Active player" });
    expect(within(activePlayer).getByText("Space")).toBeInTheDocument();
    expect(within(activePlayer).getByText("Chance")).toBeInTheDocument();
    const log = screen.getByRole("region", { name: "Game log" });
    expect(within(log).getByText(/rolled 3 \+ 4 = 7/)).toBeInTheDocument();
    expect(within(log).getByText(/moved to Chance/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("tab", { name: "Contracts" }));
    const eventHistory = await screen.findByRole("region", { name: "Contract event history" });
    expect(within(eventHistory).getByText(/DICE_ROLLED/)).toBeInTheDocument();
    expect(within(eventHistory).getByText(/PLAYER_POSITION_SET/)).toBeInTheDocument();
  }, 18_000);

  it("pins the game log to the latest entry without scrolling the whole page", async () => {
    originalScrollIntoView = HTMLElement.prototype.scrollIntoView;
    originalScrollHeightDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollHeight");
    originalScrollTopDescriptor = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollTop");
    const scrollIntoView = vi.fn();
    const scrollTopWrites: number[] = [];
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      value: scrollIntoView,
    });
    Object.defineProperty(HTMLElement.prototype, "scrollHeight", {
      configurable: true,
      get() {
        return this.hasAttribute("data-game-log-scroll-region") ? 800 : 0;
      },
    });
    Object.defineProperty(HTMLElement.prototype, "scrollTop", {
      configurable: true,
      get() {
        const storedValue = (this as HTMLElement & { __testScrollTop?: number }).__testScrollTop;
        return typeof storedValue === "number" ? storedValue : 0;
      },
      set(value: number) {
        if (this.hasAttribute("data-game-log-scroll-region")) {
          scrollTopWrites.push(value);
        }
        (this as HTMLElement & { __testScrollTop?: number }).__testScrollTop = value;
      },
    });

    renderSurface(
      baseFetchMock({
        game: metadataFallbackAiGame(),
        events: eventsFixture(acceptedRollResponse().accepted_events),
      }),
      metadataFallbackAiGame(),
    );

    const log = await screen.findByRole("region", { name: "Game log" });
    expect(log.querySelector("[data-game-log-scroll-region]")).toBeInTheDocument();
    expect(await within(log).findByText(/rolled 3 \+ 4 = 7/)).toBeInTheDocument();
    expect(await within(log).findByText(/moved to Chance/)).toBeInTheDocument();
    await waitFor(() => expect(scrollTopWrites).toContain(800));
    expect(scrollIntoView).not.toHaveBeenCalled();
  });

  it("resets leftover setup-page scroll when the game surface loads", async () => {
    const originalScrollXDescriptor = Object.getOwnPropertyDescriptor(window, "scrollX");
    const originalScrollYDescriptor = Object.getOwnPropertyDescriptor(window, "scrollY");
    const scrollTo = vi.fn();
    vi.stubGlobal("scrollTo", scrollTo);
    Object.defineProperty(window, "scrollX", {
      configurable: true,
      value: 0,
    });
    Object.defineProperty(window, "scrollY", {
      configurable: true,
      value: 679,
    });

    try {
      renderSurface(baseFetchMock());

      await screen.findByRole("region", { name: "Game log" });
      await waitFor(() =>
        expect(scrollTo).toHaveBeenCalledWith({
          behavior: "auto",
          left: 0,
          top: 0,
        }),
      );
    } finally {
      if (originalScrollXDescriptor) {
        Object.defineProperty(window, "scrollX", originalScrollXDescriptor);
      } else {
        Reflect.deleteProperty(window, "scrollX");
      }
      if (originalScrollYDescriptor) {
        Object.defineProperty(window, "scrollY", originalScrollYDescriptor);
      } else {
        Reflect.deleteProperty(window, "scrollY");
      }
    }
  });

  it("resets browser-restored setup scroll after the game surface paints", async () => {
    const originalScrollXDescriptor = Object.getOwnPropertyDescriptor(window, "scrollX");
    const originalScrollYDescriptor = Object.getOwnPropertyDescriptor(window, "scrollY");
    const scrollTo = vi.fn();
    let simulatedScrollY = 0;
    vi.stubGlobal("scrollTo", scrollTo);
    Object.defineProperty(window, "scrollX", {
      configurable: true,
      get: () => 0,
    });
    Object.defineProperty(window, "scrollY", {
      configurable: true,
      get: () => simulatedScrollY,
    });

    try {
      renderSurface(baseFetchMock());

      await screen.findByRole("region", { name: "Game log" });
      expect(scrollTo).not.toHaveBeenCalled();
      simulatedScrollY = 679;
      await waitFor(() =>
        expect(scrollTo).toHaveBeenCalledWith({
          behavior: "auto",
          left: 0,
          top: 0,
        }),
      );
    } finally {
      if (originalScrollXDescriptor) {
        Object.defineProperty(window, "scrollX", originalScrollXDescriptor);
      } else {
        Reflect.deleteProperty(window, "scrollX");
      }
      if (originalScrollYDescriptor) {
        Object.defineProperty(window, "scrollY", originalScrollYDescriptor);
      } else {
        Reflect.deleteProperty(window, "scrollY");
      }
    }
  });

  it("renders backend die_1 and die_2 dice payloads as pips and total instead of placeholders", async () => {
    let accepted = false;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(accepted ? gameFixture(7) : gameFixture(0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(accepted ? stateFixture(7, 2) : stateFixture(0, 0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: accepted ? [legalAction("END_TURN", {}, "state-2", 2)] : [legalAction("ROLL_DICE")],
          state_hash: accepted ? "state-2" : "state-0",
          event_sequence: accepted ? 2 : 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(accepted ? eventsFixture(acceptedBackendDiceRollResponse().accepted_events) : eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        accepted = true;
        return Response.json(acceptedBackendDiceRollResponse());
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: "Roll dice" }));

    const diceStatus = await screen.findByRole("status", { name: "Dice roll animation" });
    await waitFor(() => expect(diceStatus).toHaveTextContent("3 + 4 = 7"));
    expect(diceStatus).not.toHaveTextContent("?");
    expect(diceStatus.querySelector("[data-dice-value='3']")).toBeInTheDocument();
    expect(diceStatus.querySelector("[data-dice-value='4']")).toBeInTheDocument();
    expect(diceStatus.querySelectorAll("[data-dice-pip]")).toHaveLength(7);
  });

  it("moves a player token square by square and keeps dice centered until end of turn", async () => {
    let accepted = false;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(accepted ? gameFixture(5) : gameFixture(0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(accepted ? stateFixture(5, 2) : stateFixture(0, 0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: accepted ? [legalAction("END_TURN", {}, "state-2", 2)] : [legalAction("ROLL_DICE")],
          state_hash: accepted ? "state-2" : "state-0",
          event_sequence: accepted ? 2 : 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(accepted ? eventsFixture(acceptedReadingRailroadRollResponse().accepted_events) : eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        accepted = true;
        return Response.json(acceptedReadingRailroadRollResponse());
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: "Roll dice" }));

    const board = await screen.findByRole("region", { name: "Classic Monopoly-style board" });
    const expectedSteps = [
      "Ada token at Mediterranean Avenue, position 1",
      "Ada token at Community Chest, position 2",
      "Ada token at Baltic Avenue, position 3",
      "Ada token at Income Tax, position 4",
      "Ada token at Reading Railroad, position 5",
    ];
    for (const stepLabel of expectedSteps) {
      expect(await within(board).findByLabelText(stepLabel, {}, { timeout: 3_000 })).toBeVisible();
    }
    const diceStatus = within(board).getByRole("status", { name: "Dice roll animation" });
    expect(diceStatus).toHaveTextContent("2 + 3 = 5");
    await waitFor(
      () =>
        expect(within(board).getByRole("status", { name: "Board landing" })).toHaveTextContent(
          "Ada landed on Reading Railroad",
        ),
      { timeout: 3_000 },
    );
    await waitFor(
      () => expect(within(board).getByRole("status", { name: "Dice roll animation" })).toHaveAttribute("data-dice-motion", "settled"),
      {
        timeout: 5_000,
      },
    );
    expect(within(board).getByRole("status", { name: "Dice roll animation" })).toHaveTextContent("2 + 3 = 5");
    expect(board.querySelector("[data-center-motion-stack]")).toBeInTheDocument();
    expect(within(board).getByRole("status", { name: "Dice roll animation" })).toHaveAttribute(
      "data-dice-placement",
      "center-board",
    );
  }, 14_000);

  it("keeps the centered dice destination when a roll sends the player to jail", async () => {
    let accepted = false;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(accepted ? gameFixture(10) : gameFixture(24));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(accepted ? acceptedJailRollResponse() : stateFixture(24, 0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: accepted ? [legalAction("END_TURN", {}, "state-5", 5)] : [legalAction("ROLL_DICE")],
          state_hash: accepted ? "state-5" : "state-0",
          event_sequence: accepted ? 5 : 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(accepted ? eventsFixture(acceptedJailRollResponse().accepted_events) : eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        accepted = true;
        return Response.json(acceptedJailRollResponse());
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: "Roll dice" }));

    const board = await screen.findByRole("region", { name: "Classic Monopoly-style board" });
    await waitFor(
      () =>
        expect(within(board).getByRole("status", { name: "Board landing" })).toHaveTextContent(
          "Ada landed on Jail / Just Visiting",
        ),
      { timeout: 8_000 },
    );
    await waitFor(
      () => expect(within(board).getByRole("status", { name: "Dice roll animation" })).toHaveAttribute("data-dice-motion", "settled"),
      { timeout: 5_000 },
    );
    const diceStatus = within(board).getByRole("status", { name: "Dice roll animation" });
    expect(diceStatus).toHaveTextContent("2 + 4 = 6");
    expect(diceStatus).not.toHaveTextContent("GO");
  }, 14_000);

  it("clears the centered dice result once the next player's turn starts", async () => {
    let stage: "fresh" | "rolled" | "ended" = "fresh";
    const endTurnEvents = [
      ...acceptedReadingRailroadRollResponse().accepted_events,
      ...acceptedEndTurnResponse().accepted_events,
    ];
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(stage === "fresh" ? gameFixture(0) : gameFixture(5));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        if (stage === "ended") {
          return Response.json(nextTurnStateFixture(5, 3));
        }
        return Response.json(stage === "rolled" ? stateFixture(5, 2) : stateFixture(0, 0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: stage === "rolled" ? [legalAction("END_TURN", {}, "state-2", 2)] : [legalAction("ROLL_DICE")],
          state_hash: stage === "rolled" ? "state-2" : "state-0",
          event_sequence: stage === "rolled" ? 2 : 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [aiLegalAction("ROLL_DICE", {}, "state-3", 3)],
          state_hash: "state-3",
          event_sequence: 3,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        if (stage === "ended") {
          return Response.json(eventsFixture(endTurnEvents));
        }
        return Response.json(stage === "rolled" ? eventsFixture(acceptedReadingRailroadRollResponse().accepted_events) : eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        const body = JSON.parse(String(init.body));
        if (body.type === "ROLL_DICE") {
          stage = "rolled";
          return Response.json(acceptedReadingRailroadRollResponse());
        }
        if (body.type === "END_TURN") {
          stage = "ended";
          return Response.json(acceptedEndTurnResponse());
        }
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: "Roll dice" }));
    const board = await screen.findByRole("region", { name: "Classic Monopoly-style board" });
    await waitFor(
      () => expect(within(board).getByRole("status", { name: "Dice roll animation" })).toHaveAttribute("data-dice-motion", "settled"),
      { timeout: 5_000 },
    );
    expect(within(board).getByRole("status", { name: "Dice roll animation" })).toHaveTextContent("2 + 3 = 5");

    fireEvent.click(await screen.findByRole("button", { name: "End turn" }));

    await waitFor(() => expect(screen.getByRole("region", { name: "Active player" })).toHaveTextContent("Grace"));
    await waitFor(() => expect(within(board).queryByRole("status", { name: "Dice roll animation" })).not.toBeInTheDocument());
  }, 14_000);

  it("keeps the AI dice result centered if refreshed state still belongs to the same AI turn", async () => {
    const postRollEvents = acceptedAiReadingRailroadStepResponse().accepted_events;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(aiStateFixture(2));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [aiLegalAction("END_TURN", {}, "ai-state-2", 2)],
          state_hash: "ai-state-2",
          event_sequence: 2,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture(postRollEvents));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const board = await screen.findByRole("region", { name: "Classic Monopoly-style board" });
    const diceStatus = await within(board).findByRole("status", { name: "Dice roll animation" });
    expect(diceStatus).toHaveTextContent("2 + 3 = 5");
    expect(board.querySelector("[data-center-motion-stack]")).toBeInTheDocument();
    expect(diceStatus).toHaveAttribute("data-dice-placement", "center-board");
    expect(diceStatus).not.toHaveClass("right-3");
  });

  it("shows chance and community chest draws as a modal over the board", async () => {
    let accepted = false;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(accepted ? gameFixture(7) : gameFixture(0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(accepted ? stateFixture(7, 3) : stateFixture(0, 0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: accepted ? [legalAction("END_TURN", {}, "state-3", 3)] : [legalAction("ROLL_DICE")],
          state_hash: accepted ? "state-3" : "state-0",
          event_sequence: accepted ? 3 : 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(accepted ? eventsFixture(acceptedChanceCardResponse().accepted_events) : eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        accepted = true;
        return Response.json(acceptedChanceCardResponse());
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);
    fireEvent.click(await screen.findByRole("button", { name: "Roll dice" }));

    const board = await screen.findByRole("region", { name: "Classic Monopoly-style board" });
    await waitFor(() =>
      expect(within(board).getByRole("status", { name: "Dice roll animation" })).toHaveAttribute(
        "data-dice-motion",
        "rolling",
      ),
    );
    await waitFor(() =>
      expect(within(board).getByRole("status", { name: "Dice roll animation" })).toHaveTextContent("3 + 4 = 7"),
    );
    expect(within(board).queryByRole("dialog", { name: "Chance card" })).not.toBeInTheDocument();

    await waitFor(() => expect(board).toHaveAttribute("data-board-motion", "moving"), { timeout: 2_000 });
    expect(within(board).queryByRole("dialog", { name: "Chance card" })).not.toBeInTheDocument();

    const modal = await within(board).findByRole("dialog", { name: "Chance card" }, { timeout: 6_000 });
    expect(modal).toHaveTextContent("Move to GO");
    expect(modal).toHaveTextContent("Move to GO and apply the normal pass-GO payout.");
    expect(modal).toHaveTextContent("Ada");
    expect(modal).toHaveAttribute("data-card-deck", "chance");
    expect(within(modal).getByRole("img", { name: "Chance card art" })).toBeVisible();

    fireEvent.click(within(modal).getByRole("button", { name: "Dismiss card" }));
    await waitFor(() => expect(within(board).queryByRole("dialog", { name: "Chance card" })).not.toBeInTheDocument());
  }, 12_000);

  it("refreshes auction start controls from post-roll legal actions", async () => {
    let accepted = false;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(accepted ? gameFixture(1) : gameFixture(0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(accepted ? auctionPurchaseStateFixture() : stateFixture(0, 0));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: accepted
            ? [
                legalAction(
                  "START_AUCTION",
                  { property_id: "property_mediterranean_avenue" },
                  "state-2",
                  2,
                ),
              ]
            : [legalAction("ROLL_DICE")],
          state_hash: accepted ? "state-2" : "state-0",
          event_sequence: accepted ? 2 : 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(accepted ? eventsFixture(acceptedAuctionRollResponse().accepted_events) : eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        accepted = true;
        return Response.json(acceptedAuctionRollResponse());
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    fireEvent.click(await screen.findByRole("button", { name: "Roll dice" }));

    const auction = await screen.findByRole("region", { name: "Auction" });
    await waitFor(() => expect(auction).toHaveTextContent("Mediterranean Avenue"));
    expect(within(auction).getByRole("button", { name: "Start auction" })).toBeEnabled();
  });

  it("ignores SSE keepalive messages and refreshes only on accepted game events", async () => {
    vi.stubGlobal("EventSource", FakeEventSource);
    const fetchMock = baseFetchMock({
      legalActions: [legalAction("ROLL_DICE")],
    });

    renderSurface(fetchMock);

    const controls = await screen.findByRole("region", { name: "Turn controls" });
    expect(await within(controls).findByRole("button", { name: "Roll dice" })).toBeEnabled();
    expect(within(controls).queryByText("Loading moves")).not.toBeInTheDocument();

    const legalActionFetchCount = () =>
      fetchMock.mock.calls.filter(([url]) => String(url).includes("/legal-actions")).length;
    const initialLegalActionFetches = legalActionFetchCount();
    expect(initialLegalActionFetches).toBeGreaterThanOrEqual(1);
    expect(FakeEventSource.instances).toHaveLength(1);

    FakeEventSource.instances[0]?.dispatch("message");
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(legalActionFetchCount()).toBe(initialLegalActionFetches);
    expect(within(controls).getByRole("button", { name: "Roll dice" })).toBeEnabled();
    expect(within(controls).queryByText("Loading moves")).not.toBeInTheDocument();

    FakeEventSource.instances[0]?.dispatch("game_event");

    await waitFor(() => expect(legalActionFetchCount()).toBeGreaterThan(initialLegalActionFetches));
  });

  it("shows a Rejected action alert and leaves prior visible board state intact after rejection", async () => {
    renderSurface(
      baseFetchMock({
        actionResponse: rejectedRollResponse(),
      }),
    );

    expect(await screen.findByLabelText("Ada token at GO, position 0")).toBeVisible();
    fireEvent.click(await screen.findByRole("button", { name: "Roll dice" }));

    const alert = await screen.findByRole("alert", { name: "Rejected action" });
    expect(alert).toHaveTextContent("Rejected action");
    expect(alert).toHaveTextContent("stale_action");
    expect(alert).toHaveTextContent("action expected state no longer matches current state");
    expect(screen.getByLabelText("Ada token at GO, position 0")).toBeVisible();
    expect(screen.queryByLabelText("Ada token at Chance, position 7")).not.toBeInTheDocument();
  });

  it("disables controls while legal actions load and while an action submission is pending", async () => {
    let resolveLegalActions: (payload: unknown) => void = () => {};
    let resolveAction: (response: Response) => void = () => {};
    const legalActionsPayloadPromise = new Promise<unknown>((resolve) => {
      resolveLegalActions = resolve;
    });
    const actionPromise = new Promise<Response>((resolve) => {
      resolveAction = resolve;
    });
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return jsonResponse(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return jsonResponse(stateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return legalActionsPayloadPromise.then((payload) => Response.json(payload));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return jsonResponse(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return jsonResponse(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        return actionPromise;
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const controls = await screen.findByRole("region", { name: "Turn controls" });
    expect(within(controls).getByRole("button", { name: "End turn" })).toBeDisabled();
    expect(within(controls).queryByText("Loading moves")).not.toBeInTheDocument();

    resolveLegalActions({
      game_id: gameId,
      actor_player_id: adaId,
      legal_actions: [legalAction("ROLL_DICE")],
      state_hash: "state-0",
      event_sequence: 0,
    });

    const rollButton = await within(controls).findByRole("button", { name: "Roll dice" });
    fireEvent.click(rollButton);
    expect(rollButton).toBeDisabled();
    expect(rollButton).toHaveTextContent("Submitting");
    expect(screen.getByRole("status", { name: "Dice roll animation" })).toHaveTextContent("Rolling dice");

    resolveAction(Response.json(acceptedRollResponse()));
    await waitFor(() => expect(rollButton).not.toHaveTextContent("Submitting"));
  });

  it("submits the Manual AI step control for the active AI player", async () => {
    // Manual AI step control
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const stepButton = await screen.findByRole("button", { name: "Step AI" });
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);

    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI done"));
    const aiStepCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
    );
    expect(JSON.parse(String(aiStepCall?.[1]?.body))).toMatchObject({
      player_id: graceId,
      decision_type: "action_decision",
      mandatory: true,
      request_context: { mode: "manual" },
    });
  });

  it("keeps the last AI step status visible after the turn advances to a human", async () => {
    let aiStepCompleted = false;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(aiStepCompleted ? stateFixture(0, 1) : aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: [legalAction("ROLL_DICE", {}, "state-1", 1)],
          state_hash: "state-1",
          event_sequence: 1,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        aiStepCompleted = true;
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const stepButton = await screen.findByRole("button", { name: "Step AI" });
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);

    await waitFor(() => expect(screen.getByRole("region", { name: "Active player" })).toHaveTextContent("Ada"));
    expect(screen.queryByRole("button", { name: "Step AI" })).not.toBeInTheDocument();
    expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI done");
  });

  it("shows AI dice rolls with pips and total when an AI step accepts roll events", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(acceptedAiDiceStepResponse());
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const stepButton = await screen.findByRole("button", { name: "Step AI" });
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);

    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI done"));
    const board = await screen.findByRole("region", { name: "Classic Monopoly-style board" });
    await waitFor(() => expect(board).toHaveAttribute("data-board-motion", "moving"), { timeout: 2_000 });
    const movingGrace = within(board).getByLabelText("Grace token at GO, position 0");
    expect(movingGrace).toHaveAttribute("data-token-motion-overlay", "true");
    expect(movingGrace).toHaveAttribute("data-token-slide", "true");
    const diceStatus = within(board).getByRole("status", { name: "Dice roll animation" });
    expect(diceStatus).toHaveTextContent("5 + 2 = 7");
    expect(diceStatus).not.toHaveTextContent("?");
    expect(diceStatus.querySelector("[data-dice-value='5']")).toBeInTheDocument();
    expect(diceStatus.querySelector("[data-dice-value='2']")).toBeInTheDocument();
    expect(diceStatus.querySelectorAll("[data-dice-pip]")).toHaveLength(7);
  });

  it("moves an AI token square by square and keeps the AI dice result centered", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(acceptedAiReadingRailroadStepResponse());
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const stepButton = await screen.findByRole("button", { name: "Step AI" });
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);

    const board = await screen.findByRole("region", { name: "Classic Monopoly-style board" });
    const expectedSteps = [
      "Grace token at Mediterranean Avenue, position 1",
      "Grace token at Community Chest, position 2",
      "Grace token at Baltic Avenue, position 3",
      "Grace token at Income Tax, position 4",
      "Grace token at Reading Railroad, position 5",
    ];
    for (const stepLabel of expectedSteps) {
      expect(await within(board).findByLabelText(stepLabel, {}, { timeout: 3_000 })).toBeVisible();
    }
    await waitFor(
      () =>
        expect(within(board).getByRole("status", { name: "Board landing" })).toHaveTextContent(
          "Grace landed on Reading Railroad",
        ),
      { timeout: 3_000 },
    );
    const diceStatus = within(board).getByRole("status", { name: "Dice roll animation" });
    expect(diceStatus).toHaveTextContent("2 + 3 = 5");
    expect(diceStatus).toHaveAttribute("data-dice-motion", "settled");
    expect(board.querySelector("[data-center-motion-stack]")).toBeInTheDocument();
    expect(diceStatus).toHaveAttribute("data-dice-placement", "center-board");
    expect(diceStatus).not.toHaveClass("right-3");
  }, 14_000);

  it("keeps an AI roll visible through same-turn AI follow-up actions", async () => {
    let aiStepCount = 0;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      const postRollState = aiPostRollStateFixture(aiStepCount >= 2 ? 3 : 2);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(aiStepCount > 0 ? postRollState : aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions:
            aiStepCount > 0
              ? [aiLegalAction("BUY_PROPERTY", { property_id: "property_reading_railroad" }, postRollState.state_hash, postRollState.event_sequence)]
              : [aiLegalAction("ROLL_DICE")],
          state_hash: aiStepCount > 0 ? postRollState.state_hash : "ai-state-0",
          event_sequence: aiStepCount > 0 ? postRollState.event_sequence : 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        aiStepCount += 1;
        return Response.json(aiStepCount === 1 ? acceptedAiReadingRailroadStepResponse() : acceptedAiPropertyFollowUpStepResponse());
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const stepButton = await screen.findByRole("button", { name: "Step AI" });
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);

    const board = await screen.findByRole("region", { name: "Classic Monopoly-style board" });
    await waitFor(() => expect(within(board).getByRole("status", { name: "Dice roll animation" })).toHaveTextContent("2 + 3 = 5"));

    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);

    await waitFor(() => {
      const aiStepCalls = fetchMock.mock.calls.filter(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
      );
      expect(aiStepCalls).toHaveLength(2);
    });
    expect(within(board).getByRole("status", { name: "Dice roll animation" })).toHaveTextContent("2 + 3 = 5");
    expect(board.querySelector("[data-center-motion-stack]")).toBeInTheDocument();
    expect(within(board).getByRole("status", { name: "Dice roll animation" })).toHaveAttribute(
      "data-dice-placement",
      "center-board",
    );
  });

  it("refetches AI audit records after a successful Manual AI step", async () => {
    let resolveAiStep: (response: Response) => void = () => {};
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const url = String(input);
      const aiAuditResponse = aiAuditResponses.find((response) => url === aiAuditUrl(response.path));
      if (aiAuditResponse) {
        return jsonResponse(aiAuditResponse.payload);
      }
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return jsonResponse(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return jsonResponse(aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return jsonResponse({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return jsonResponse(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return jsonResponse(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return new Promise<Response>((resolve) => {
          resolveAiStep = resolve;
        });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    fireEvent.click(await screen.findByRole("tab", { name: "AI notebook" }));

    for (const response of aiAuditResponses) {
      await waitFor(() => expect(aiAuditFetchCount(fetchMock, response.path)).toBe(1));
    }

    const stepButton = await screen.findByRole("button", { name: "Step AI" });
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);

    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI thinking"));
    for (const response of aiAuditResponses) {
      expect(aiAuditFetchCount(fetchMock, response.path)).toBe(1);
    }

    resolveAiStep(Response.json(aiStepResponse("done")));

    await waitFor(() => {
      for (const response of aiAuditResponses) {
        expect(aiAuditFetchCount(fetchMock, response.path)).toBe(2);
      }
    });

    const aiStepCallIndex = fetchMock.mock.calls.findIndex(
      ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
    );
    for (const response of aiAuditResponses) {
      let refetchCallIndex = -1;
      fetchMock.mock.calls.forEach(([url], index) => {
        if (String(url) === aiAuditUrl(response.path)) {
          refetchCallIndex = index;
        }
      });
      expect(refetchCallIndex).toBeGreaterThan(aiStepCallIndex);
    }
  });

  it("Manual AI step waits for loaded turn state", async () => {
    let resolveState: (response: Response) => void = () => {};
    const statePromise = new Promise<Response>((resolve) => {
      resolveState = resolve;
    });
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return jsonResponse(metadataFallbackAiGame());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return statePromise;
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return jsonResponse({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: [],
          state_hash: "metadata-fallback-state",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return jsonResponse({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return jsonResponse(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return jsonResponse(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return jsonResponse(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock, metadataFallbackAiGame());

    expect(await screen.findByRole("region", { name: "Turn controls" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Step AI" })).not.toBeInTheDocument();
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
      ),
    ).toBe(false);

    resolveState(Response.json(aiStateFixture()));

    const stepButton = await screen.findByRole("button", { name: "Step AI" });
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);

    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI done"));
    const aiStepCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
    );
    expect(JSON.parse(String(aiStepCall?.[1]?.body))).toMatchObject({
      player_id: graceId,
      decision_type: "action_decision",
      mandatory: true,
      request_context: { mode: "manual" },
    });
  });

  it("Lifecycle-only AI step rejections parse into status panel results", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(
          {
            status: "rejected",
            reason_code: "game_ai_blocked",
            validation_errors: [
              {
                code: "game_ai_blocked",
                message: "AI stepping is blocked for this game lifecycle state.",
                field: "game_id",
              },
            ],
          },
          { status: 409 },
        );
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const stepButton = await screen.findByRole("button", { name: "Step AI" });
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);

    const status = await screen.findByRole("status", { name: "AI step status" });
    await waitFor(() => expect(status).toHaveTextContent("AI rejected"));
    expect(status).toHaveTextContent("game_ai_blocked");
  });

  it("disables direct action controls for AI turns", async () => {
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [aiLegalAction("ROLL_DICE")],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        throw new Error("AI direct actions must not be submitted from turn controls");
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const controls = await screen.findByRole("region", { name: "Turn controls" });
    const rollButton = await within(controls).findByRole("button", { name: "Roll dice" });
    expect(rollButton).toBeDisabled();
    fireEvent.click(rollButton);
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST",
      ),
    ).toBe(false);

    const stepButton = within(controls).getByRole("button", { name: "Step AI" });
    expect(stepButton).toBeEnabled();
    fireEvent.click(stepButton);

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
        ),
      ).toBe(true),
    );
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST",
      ),
    ).toBe(false);
  });

  it("preserves human auction controls during mixed AI turns", async () => {
    const auctionState = mixedAuctionAiTurnStateFixture();
    const humanBid = legalAction(
      "BID_AUCTION",
      { property_id: "property_mediterranean_avenue", amount: 26 },
      auctionState.state_hash,
      auctionState.event_sequence,
    );
    const humanPass = legalAction(
      "PASS_AUCTION",
      { property_id: "property_mediterranean_avenue" },
      auctionState.state_hash,
      auctionState.event_sequence,
    );
    const aiBid = aiLegalAction(
      "BID_AUCTION",
      { property_id: "property_mediterranean_avenue", amount: 27 },
      auctionState.state_hash,
      auctionState.event_sequence,
    );
    const aiPass = aiLegalAction(
      "PASS_AUCTION",
      { property_id: "property_mediterranean_avenue" },
      auctionState.state_hash,
      auctionState.event_sequence,
    );

    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture(1));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(auctionState);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: [humanBid, humanPass],
          state_hash: auctionState.state_hash,
          event_sequence: auctionState.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [aiBid, aiPass],
          state_hash: auctionState.state_hash,
          event_sequence: auctionState.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        return Response.json({
          status: "accepted",
          game_id: gameId,
          accepted_events: [
            {
              id: "event-auction-bid",
              game_id: gameId,
              sequence: auctionState.event_sequence + 1,
              actor_player_id: adaId,
              event_type: "AUCTION_BID_PLACED",
              payload: { property_id: "property_mediterranean_avenue", bidder_id: adaId, amount: 26 },
              state_hash: "auction-ai-state-after-human-bid",
              created_at: "2026-07-04T00:02:00.000Z",
            },
          ],
          state: {
            ...auctionState.state,
            active_auction: {
              property_id: "property_mediterranean_avenue",
              high_bidder_id: adaId,
              high_bid_amount: 26,
              passed_player_ids: [],
            },
          },
          state_hash: "auction-ai-state-after-human-bid",
          event_sequence: auctionState.event_sequence + 1,
        });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const auction = await screen.findByRole("region", { name: "Auction" });
    const adaControls = await within(auction).findByRole("group", { name: "Ada auction controls" });
    const graceControls = await within(auction).findByRole("group", { name: "Grace auction controls" });
    const adaBidButton = within(adaControls).getByRole("button", { name: "Bid" });
    const graceBidButton = within(graceControls).getByRole("button", { name: "Bid" });
    const gracePassButton = within(graceControls).getByRole("button", { name: "Pass" });

    await waitFor(() => expect(adaBidButton).toBeEnabled());
    expect(graceBidButton).toBeDisabled();
    expect(gracePassButton).toBeDisabled();

    fireEvent.click(adaBidButton);

    await waitFor(() => {
      const actionCalls = fetchMock.mock.calls.filter(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST",
      );
      expect(actionCalls).toHaveLength(1);
      expect(JSON.parse(String(actionCalls[0]?.[1]?.body))).toMatchObject({
        actor_id: adaId,
        type: "BID_AUCTION",
        payload: { property_id: "property_mediterranean_avenue", amount: 26 },
      });
    });

    fireEvent.click(graceBidButton);
    fireEvent.click(gracePassButton);

    const actionBodies = fetchMock.mock.calls
      .filter(([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST")
      .map(([, init]) => JSON.parse(String(init?.body)));
    expect(actionBodies.some((body) => body.actor_id === graceId)).toBe(false);
  });

  it("lets AI bidders step during human-started auctions", async () => {
    const auctionState = mixedAuctionHumanTurnStateFixture();
    const humanBid = legalAction(
      "BID_AUCTION",
      { property_id: "property_mediterranean_avenue", amount: 26 },
      auctionState.state_hash,
      auctionState.event_sequence,
    );
    const humanPass = legalAction(
      "PASS_AUCTION",
      { property_id: "property_mediterranean_avenue" },
      auctionState.state_hash,
      auctionState.event_sequence,
    );
    const aiBid = aiLegalAction(
      "BID_AUCTION",
      { property_id: "property_mediterranean_avenue", amount: 27 },
      auctionState.state_hash,
      auctionState.event_sequence,
    );
    const aiPass = aiLegalAction(
      "PASS_AUCTION",
      { property_id: "property_mediterranean_avenue" },
      auctionState.state_hash,
      auctionState.event_sequence,
    );

    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture(1));
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(auctionState);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: [humanBid, humanPass],
          state_hash: auctionState.state_hash,
          event_sequence: auctionState.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [aiBid, aiPass],
          state_hash: auctionState.state_hash,
          event_sequence: auctionState.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        return Response.json({
          status: "accepted",
          game_id: gameId,
          accepted_events: [
            {
              id: "event-human-auction-bid",
              game_id: gameId,
              sequence: auctionState.event_sequence + 1,
              actor_player_id: adaId,
              event_type: "AUCTION_BID_PLACED",
              payload: { property_id: "property_mediterranean_avenue", bidder_id: adaId, amount: 26 },
              state_hash: "auction-human-state-after-bid",
              created_at: "2026-07-04T00:02:00.000Z",
            },
          ],
          state: {
            ...auctionState.state,
            active_auction: {
              property_id: "property_mediterranean_avenue",
              high_bidder_id: adaId,
              high_bid_amount: 26,
              passed_player_ids: [],
            },
          },
          state_hash: "auction-human-state-after-bid",
          event_sequence: auctionState.event_sequence + 1,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const auction = await screen.findByRole("region", { name: "Auction" });
    const adaControls = await within(auction).findByRole("group", { name: "Ada auction controls" });
    const graceControls = await within(auction).findByRole("group", { name: "Grace auction controls" });
    const adaBidButton = within(adaControls).getByRole("button", { name: "Bid" });
    const graceBidButton = within(graceControls).getByRole("button", { name: "Bid" });
    const gracePassButton = within(graceControls).getByRole("button", { name: "Pass" });

    await waitFor(() => expect(adaBidButton).toBeEnabled());
    expect(graceBidButton).toBeDisabled();
    expect(gracePassButton).toBeDisabled();

    fireEvent.click(adaBidButton);

    await waitFor(() => {
      const actionCalls = fetchMock.mock.calls.filter(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST",
      );
      expect(actionCalls).toHaveLength(1);
      expect(JSON.parse(String(actionCalls[0]?.[1]?.body))).toMatchObject({
        actor_id: adaId,
        type: "BID_AUCTION",
        payload: { property_id: "property_mediterranean_avenue", amount: 26 },
      });
    });

    fireEvent.click(graceBidButton);
    fireEvent.click(gracePassButton);

    const aiStepButton = await within(graceControls).findByRole("button", { name: "Step AI" });
    await waitFor(() => expect(aiStepButton).toBeEnabled());
    fireEvent.click(aiStepButton);

    await waitFor(() => {
      const actionBodies = fetchMock.mock.calls
        .filter(([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST")
        .map(([, init]) => JSON.parse(String(init?.body)));
      expect(actionBodies.some((body) => body.actor_id === graceId)).toBe(false);

      const aiStepCalls = fetchMock.mock.calls.filter(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
      );
      expect(aiStepCalls.length).toBeGreaterThanOrEqual(1);
      expect(JSON.parse(String(aiStepCalls[0]?.[1]?.body))).toMatchObject({
        player_id: graceId,
        decision_type: "action_decision",
        mandatory: true,
        request_context: { mode: "auction_ai_bidder" },
      });
    });
  });

  it("disables gameplay controls when AI_BLOCKED", async () => {
    const auctionState = mixedAuctionHumanTurnStateFixture();
    const blockedGame = {
      ...gameFixture(1),
      status: "AI_BLOCKED",
    };
    const blockedLegalAction = (type: string, payload: Record<string, unknown> = {}) =>
      legalAction(type, payload, auctionState.state_hash, auctionState.event_sequence);
    const blockedAiLegalAction = (type: string, payload: Record<string, unknown> = {}) =>
      aiLegalAction(type, payload, auctionState.state_hash, auctionState.event_sequence);
    const humanBid = blockedLegalAction("BID_AUCTION", {
      property_id: "property_mediterranean_avenue",
      amount: 26,
    });
    const humanPass = blockedLegalAction("PASS_AUCTION", {
      property_id: "property_mediterranean_avenue",
    });
    const aiBid = blockedAiLegalAction("BID_AUCTION", {
      property_id: "property_mediterranean_avenue",
      amount: 27,
    });
    const aiPass = blockedAiLegalAction("PASS_AUCTION", {
      property_id: "property_mediterranean_avenue",
    });
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(blockedGame);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(auctionState);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: [
            blockedLegalAction("ROLL_DICE"),
            blockedLegalAction("END_TURN"),
            blockedLegalAction("MORTGAGE_PROPERTY", { property_id: "property_mediterranean_avenue" }),
            blockedLegalAction("UNMORTGAGE_PROPERTY", { property_id: "property_mediterranean_avenue" }),
            blockedLegalAction("BUY_HOUSE", { property_id: "property_mediterranean_avenue" }),
            blockedLegalAction("SELL_HOUSE", { property_id: "property_mediterranean_avenue" }),
            humanBid,
            humanPass,
          ],
          state_hash: auctionState.state_hash,
          event_sequence: auctionState.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [aiBid, aiPass],
          state_hash: auctionState.state_hash,
          event_sequence: auctionState.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST") {
        return Response.json(acceptedRollResponse());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock, blockedGame);

    const controls = await screen.findByRole("region", { name: "Turn controls" });
    const rollButton = await within(controls).findByRole("button", { name: "Roll dice" });
    const endTurnButton = within(controls).getByRole("button", { name: "End turn" });
    expect(rollButton).toBeDisabled();
    expect(endTurnButton).toBeDisabled();
    expect(within(controls).queryByRole("button", { name: "Step AI" })).not.toBeInTheDocument();
    expect(within(controls).queryByRole("checkbox", { name: "Auto-step AI" })).not.toBeInTheDocument();

    const management = await screen.findByRole("region", { name: "Property management" });
    const mortgageButton = await within(management).findByRole("button", { name: "Mortgage" });
    const unmortgageButton = within(management).getByRole("button", { name: "Unmortgage" });
    const buildButton = within(management).getByRole("button", { name: "Build house" });
    const sellButton = within(management).getByRole("button", { name: "Sell house" });
    expect(mortgageButton).toBeDisabled();
    expect(unmortgageButton).toBeDisabled();
    expect(buildButton).toBeDisabled();
    expect(sellButton).toBeDisabled();

    const auction = await screen.findByRole("region", { name: "Auction" });
    const adaControls = await within(auction).findByRole("group", { name: "Ada auction controls" });
    const graceControls = await within(auction).findByRole("group", { name: "Grace auction controls" });
    const adaBidButton = within(adaControls).getByRole("button", { name: "Bid" });
    const adaPassButton = within(adaControls).getByRole("button", { name: "Pass" });
    const graceBidButton = within(graceControls).getByRole("button", { name: "Bid" });
    const gracePassButton = within(graceControls).getByRole("button", { name: "Pass" });
    const aiBidderStepButton = await within(graceControls).findByRole("button", { name: "Step AI" });
    expect(adaBidButton).toBeDisabled();
    expect(adaPassButton).toBeDisabled();
    expect(graceBidButton).toBeDisabled();
    expect(gracePassButton).toBeDisabled();
    expect(aiBidderStepButton).toBeDisabled();

    for (const control of [
      rollButton,
      endTurnButton,
      mortgageButton,
      unmortgageButton,
      buildButton,
      sellButton,
      adaBidButton,
      adaPassButton,
      graceBidButton,
      gracePassButton,
      aiBidderStepButton,
    ]) {
      fireEvent.click(control);
    }

    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/actions` && init?.method === "POST",
      ),
    ).toBe(false);
    expect(
      fetchMock.mock.calls.some(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
      ),
    ).toBe(false);
  });

  it("runs the Automatic AI step control while an active AI player is idle", async () => {
    // Automatic AI step control
    const fetchMock = baseFetchMock({
      state: aiStateFixture(),
      legalActions: [],
    });
    fetchMock.mockImplementation(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Auto-step AI" }));

    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(
          ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
        ),
      ).toBe(true),
    );
    expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI done");
  });

  it("auto-step asks an AI to open a negotiation for a visible near-monopoly before ordinary actions", async () => {
    const game = metadataFallbackAiGame();
    const state = aiNearMonopolyStateFixture();
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(game);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(state);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: [legalAction("ROLL_DICE", {}, state.state_hash, state.event_sequence)],
          state_hash: state.state_hash,
          event_sequence: state.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/negotiations`) {
        return Response.json({ negotiations: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/deals`) {
        return Response.json({ deals: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock, game);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Auto-step AI" }));

    await waitFor(() => {
      const aiStepCalls = fetchMock.mock.calls.filter(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
      );
      expect(aiStepCalls).toHaveLength(1);
      expect(JSON.parse(String(aiStepCalls[0]?.[1]?.body))).toMatchObject({
        player_id: adaId,
        decision_type: "open_negotiation",
        mandatory: false,
        request_context: {
          mode: "auto_negotiation",
          trade_opportunity: {
            kind: "complete_street_group",
            group: "orange",
            target_property_id: "property_tennessee_avenue",
            target_owner_id: graceId,
          },
        },
      });
    });
  });

  it("auto-step prioritizes the strongest visible near-monopoly negotiation", async () => {
    const game = metadataFallbackAiGame();
    const state = aiMultipleNearMonopoliesStateFixture();
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(game);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(state);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: [legalAction("ROLL_DICE", {}, state.state_hash, state.event_sequence)],
          state_hash: state.state_hash,
          event_sequence: state.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/negotiations`) {
        return Response.json({ negotiations: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/deals`) {
        return Response.json({ deals: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock, game);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Auto-step AI" }));

    await waitFor(() => {
      const aiStepCalls = fetchMock.mock.calls.filter(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
      );
      expect(aiStepCalls.length).toBeGreaterThanOrEqual(1);
      expect(JSON.parse(String(aiStepCalls[0]?.[1]?.body))).toMatchObject({
        player_id: adaId,
        decision_type: "open_negotiation",
        mandatory: false,
        request_context: {
          mode: "auto_negotiation",
          trade_opportunity: {
            kind: "complete_street_group",
            group: "orange",
            target_property_id: "property_tennessee_avenue",
            target_owner_id: graceId,
          },
        },
      });
    });
  });

  it("auto-step asks an AI to open a negotiation for a visible near-railroad set before ordinary actions", async () => {
    const game = metadataFallbackAiGame();
    const state = aiNearRailroadSetStateFixture();
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(game);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(state);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: [legalAction("ROLL_DICE", {}, state.state_hash, state.event_sequence)],
          state_hash: state.state_hash,
          event_sequence: state.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/negotiations`) {
        return Response.json({ negotiations: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/deals`) {
        return Response.json({ deals: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock, game);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Auto-step AI" }));

    await waitFor(() => {
      const aiStepCalls = fetchMock.mock.calls.filter(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
      );
      expect(aiStepCalls).toHaveLength(1);
      expect(JSON.parse(String(aiStepCalls[0]?.[1]?.body))).toMatchObject({
        player_id: adaId,
        decision_type: "open_negotiation",
        mandatory: false,
        request_context: {
          mode: "auto_negotiation",
          trade_opportunity: {
            kind: "complete_railroad_group",
            group: "railroad",
            target_property_id: "property_short_line_railroad",
            target_owner_id: graceId,
          },
        },
      });
    });
  });

  it("auto-step asks an AI to open a blocking negotiation when another AI is near monopoly", async () => {
    const game = metadataThreeAiGame();
    const state = aiBlockOpponentNearMonopolyStateFixture();
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(game);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(state);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: [legalAction("ROLL_DICE", {}, state.state_hash, state.event_sequence)],
          state_hash: state.state_hash,
          event_sequence: state.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/negotiations`) {
        return Response.json({ negotiations: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/deals`) {
        return Response.json({ deals: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock, game);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Auto-step AI" }));

    await waitFor(() => {
      const aiStepCalls = fetchMock.mock.calls.filter(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
      );
      expect(aiStepCalls).toHaveLength(1);
      expect(JSON.parse(String(aiStepCalls[0]?.[1]?.body))).toMatchObject({
        player_id: adaId,
        decision_type: "open_negotiation",
        mandatory: false,
        request_context: {
          mode: "auto_negotiation",
          trade_opportunity: {
            kind: "block_opponent_street_group",
            group: "orange",
            target_property_id: "property_tennessee_avenue",
            target_owner_id: linId,
            opponent_player_id: graceId,
          },
        },
      });
    });
  });

  it("auto-step blocks a high-value opponent set before completing a low-value owned set", async () => {
    const game = metadataThreeAiGame();
    const state = aiCompletionAndHigherValueBlockStateFixture();
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(game);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(state);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: [legalAction("ROLL_DICE", {}, state.state_hash, state.event_sequence)],
          state_hash: state.state_hash,
          event_sequence: state.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/negotiations`) {
        return Response.json({ negotiations: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/deals`) {
        return Response.json({ deals: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock, game);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Auto-step AI" }));

    await waitFor(() => {
      const aiStepCalls = fetchMock.mock.calls.filter(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
      );
      expect(aiStepCalls.length).toBeGreaterThanOrEqual(1);
      expect(JSON.parse(String(aiStepCalls[0]?.[1]?.body))).toMatchObject({
        player_id: adaId,
        decision_type: "open_negotiation",
        mandatory: false,
        request_context: {
          mode: "auto_negotiation",
          trade_opportunity: {
            kind: "block_opponent_street_group",
            group: "orange",
            target_property_id: "property_tennessee_avenue",
            target_owner_id: linId,
            opponent_player_id: graceId,
          },
        },
      });
    });
  });

  it("auto-step asks an AI to propose a deal for an opened AI negotiation", async () => {
    const game = metadataFallbackAiGame();
    const state = aiStateFixture();
    const negotiation = {
      id: "neg-auto-1",
      game_id: gameId,
      opened_by_player_id: adaId,
      participant_player_ids: [adaId, graceId],
      topic: "Orange completion",
      context: "Ada AI should package a trade for Tennessee Avenue.",
      status: "opened",
      round_number: 0,
      pending_deal_id: null,
      current_deal_id: null,
      acceptances: {},
      invalidated_acceptances: {},
      created_at: createdAt,
      updated_at: createdAt,
    };
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(game);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(state);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [aiLegalAction("ROLL_DICE", {}, state.state_hash, state.event_sequence)],
          state_hash: state.state_hash,
          event_sequence: state.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/negotiations`) {
        return Response.json({ negotiations: [negotiation] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/deals`) {
        return Response.json({ deals: [] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock, game);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Auto-step AI" }));

    await waitFor(() => {
      const aiStepCalls = fetchMock.mock.calls.filter(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
      );
      expect(aiStepCalls.length).toBeGreaterThanOrEqual(1);
      expect(JSON.parse(String(aiStepCalls[0]?.[1]?.body))).toMatchObject({
        player_id: adaId,
        decision_type: "deal_proposal",
        negotiation_id: "neg-auto-1",
        mandatory: false,
        request_context: {
          mode: "auto_negotiation",
          selected_deal_id: null,
        },
      });
    });
  });

  it("auto-step executes an accepted all-AI negotiation before ordinary actions", async () => {
    const game = metadataFallbackAiGame();
    const state = aiStateFixture();
    const negotiation = {
      id: "neg-auto-accepted",
      game_id: gameId,
      opened_by_player_id: adaId,
      participant_player_ids: [adaId, graceId],
      topic: "Orange completion",
      context: "Ada AI should receive Tennessee Avenue.",
      status: "accepted",
      round_number: 1,
      pending_deal_id: null,
      current_deal_id: "deal-auto-accepted",
      acceptances: { "deal-auto-accepted": [adaId, graceId] },
      invalidated_acceptances: {},
      created_at: createdAt,
      updated_at: createdAt,
    };
    const acceptedDeal = {
      id: "deal-auto-accepted",
      game_id: gameId,
      negotiation_id: "neg-auto-accepted",
      proposer_player_id: adaId,
      participant_player_ids: [adaId, graceId],
      parent_deal_id: null,
      version: 1,
      status: "accepted",
      terms: [
        {
          kind: "immediate_property_transfer",
          from_player_id: graceId,
          to_player_id: adaId,
          property_id: "property_tennessee_avenue",
        },
      ],
      validation_errors: [],
      accepted_at: createdAt,
      rejected_at: null,
      created_at: createdAt,
      updated_at: createdAt,
    };
    const executedNegotiation = {
      ...negotiation,
      status: "executed",
      updated_at: "2026-07-04T00:02:00.000Z",
    };
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(game);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(state);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [aiLegalAction("ROLL_DICE", {}, state.state_hash, state.event_sequence)],
          state_hash: state.state_hash,
          event_sequence: state.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/negotiations`) {
        return Response.json({ negotiations: [negotiation] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/deals`) {
        return Response.json({ deals: [acceptedDeal] });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/negotiations/neg-auto-accepted/execute` && init?.method === "POST") {
        return Response.json(executedNegotiation);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock, game);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Auto-step AI" }));

    await waitFor(() => {
      const executeCalls = fetchMock.mock.calls.filter(
        ([url, init]) =>
          String(url) === `${apiBaseUrl}/games/${gameId}/negotiations/neg-auto-accepted/execute` &&
          init?.method === "POST",
      );
      expect(executeCalls).toHaveLength(1);
    });
    const executeCallIndex = fetchMock.mock.calls.findIndex(
      ([url, init]) =>
        String(url) === `${apiBaseUrl}/games/${gameId}/negotiations/neg-auto-accepted/execute` &&
        init?.method === "POST",
    );
    const firstAiStepIndex = fetchMock.mock.calls.findIndex(
      ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
    );
    expect(firstAiStepIndex === -1 || executeCallIndex < firstAiStepIndex).toBe(true);
  });

  it("does not auto-step the AI again while dice and token motion are still running", async () => {
    let aiStepCount = 0;
    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      const postRollState = aiPostRollStateFixture(2);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(aiStepCount > 0 ? postRollState : aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: aiStepCount > 0 ? [aiLegalAction("END_TURN", {}, postRollState.state_hash, postRollState.event_sequence)] : [],
          state_hash: aiStepCount > 0 ? postRollState.state_hash : "ai-state-0",
          event_sequence: aiStepCount > 0 ? postRollState.event_sequence : 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        aiStepCount += 1;
        return Response.json(aiStepCount === 1 ? acceptedAiReadingRailroadStepResponse() : aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Auto-step AI" }));

    const aiStepCalls = () =>
      fetchMock.mock.calls.filter(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
      );
    await waitFor(() => expect(aiStepCalls()).toHaveLength(1));

    const board = await screen.findByRole("region", { name: "Classic Monopoly-style board" });
    await waitFor(() => expect(within(board).getByRole("status", { name: "Dice roll animation" })).toHaveTextContent("2 + 3 = 5"));
    await new Promise((resolve) => window.setTimeout(resolve, 250));

    expect(aiStepCalls()).toHaveLength(1);
    expect(within(board).getByRole("status", { name: "Dice roll animation" })).not.toHaveAttribute("data-dice-motion", "settled");
  });

  it("auto-steps the AI bidder with auction legal actions instead of the high bidder", async () => {
    const game = metadataFallbackAiGame();
    const auctionState = auctionHighBidderAiTurnStateFixture();
    const graceBid = aiLegalAction(
      "BID_AUCTION",
      { property_id: "property_mediterranean_avenue", amount: 27 },
      auctionState.state_hash,
      auctionState.event_sequence,
    );
    const gracePass = aiLegalAction(
      "PASS_AUCTION",
      { property_id: "property_mediterranean_avenue" },
      auctionState.state_hash,
      auctionState.event_sequence,
    );

    const fetchMock = vi.fn<typeof fetch>(async (input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return Response.json(game);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return Response.json(auctionState);
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${adaId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: adaId,
          legal_actions: [],
          state_hash: auctionState.state_hash,
          event_sequence: auctionState.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return Response.json({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [graceBid, gracePass],
          state_hash: auctionState.state_hash,
          event_sequence: auctionState.event_sequence,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return Response.json(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return Response.json(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return Response.json(aiStepResponse("done"));
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock, game);

    fireEvent.click(await screen.findByRole("checkbox", { name: "Auto-step AI" }));

    await waitFor(() => {
      const aiStepCalls = fetchMock.mock.calls.filter(
        ([url, init]) => String(url) === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST",
      );
      expect(aiStepCalls).toHaveLength(1);
      expect(JSON.parse(String(aiStepCalls[0]?.[1]?.body))).toMatchObject({
        player_id: graceId,
        request_context: { mode: "auction_ai_bidder" },
      });
    });
  });

  it("shows UI indication when AI is thinking, rejected, blocked, or done", async () => {
    // UI indication when AI is thinking, rejected, blocked, or done
    let resolveAiStep: (response: Response) => void = () => {};
    let aiStepPayload = aiStepResponse("rejected");
    const fetchMock = vi.fn<typeof fetch>((input, init) => {
      const url = String(input);
      if (url === `${apiBaseUrl}/games/${gameId}`) {
        return jsonResponse(gameFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/state`) {
        return jsonResponse(aiStateFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/legal-actions?actor_player_id=${graceId}`) {
        return jsonResponse({
          game_id: gameId,
          actor_player_id: graceId,
          legal_actions: [],
          state_hash: "ai-state-0",
          event_sequence: 0,
        });
      }
      if (url === `${apiBaseUrl}/games/${gameId}/events`) {
        return jsonResponse(eventsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/rejected-actions`) {
        return jsonResponse(rejectedActionsFixture());
      }
      if (url === `${apiBaseUrl}/games/${gameId}/ai/step` && init?.method === "POST") {
        return new Promise<Response>((resolve) => {
          resolveAiStep = resolve;
        });
      }
      throw new Error(`Unexpected fetch ${url}`);
    });

    renderSurface(fetchMock);

    const stepButton = await screen.findByRole("button", { name: "Step AI" });
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);
    expect(await screen.findByRole("status", { name: "AI step status" })).toHaveTextContent("AI thinking");
    resolveAiStep(Response.json(aiStepPayload));
    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI rejected"));

    aiStepPayload = aiStepResponse("blocked");
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);
    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI thinking"));
    resolveAiStep(Response.json(aiStepPayload));
    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI blocked"));

    aiStepPayload = aiStepResponse("done");
    await waitFor(() => expect(stepButton).toBeEnabled());
    fireEvent.click(stepButton);
    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI thinking"));
    resolveAiStep(Response.json(aiStepPayload));
    await waitFor(() => expect(screen.getByRole("status", { name: "AI step status" })).toHaveTextContent("AI done"));
  });
});
