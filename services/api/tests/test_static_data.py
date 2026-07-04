from __future__ import annotations

import json
from copy import deepcopy

import pytest
from pydantic import ValidationError

from app.rules.static_data import ClassicMonopolyData, load_classic_monopoly_data


def test_classic_static_data_loads_and_serializes_for_frontend_display() -> None:
    data = load_classic_monopoly_data()

    assert data.version == "classic-monopoly-v1"
    assert data.currency.code == "M"
    assert data.bank_inventory.houses == 32
    assert data.bank_inventory.hotels == 12

    json_payload = json.loads(data.model_dump_json())
    assert json_payload["board"][0]["name"] == "GO"
    assert json_payload["properties"][0]["id"].startswith("property_")
    assert json_payload["decks"]["chance"][0]["id"].startswith("card_")


def test_board_layout_has_classic_positions_and_valid_property_references() -> None:
    data = load_classic_monopoly_data()

    assert len(data.board) == 40
    assert [space.position for space in data.board] == list(range(40))
    assert len({space.id for space in data.board}) == 40

    by_position = {space.position: space for space in data.board}
    assert by_position[0].type == "go"
    assert by_position[1].name == "Mediterranean Avenue"
    assert by_position[1].property_id == "property_mediterranean_avenue"
    assert by_position[2].type == "community_chest"
    assert by_position[2].deck == "community_chest"
    assert by_position[4].type == "tax"
    assert by_position[4].amount == 200
    assert by_position[5].property_id == "property_reading_railroad"
    assert by_position[10].type == "jail"
    assert by_position[20].type == "free_parking"
    assert by_position[30].type == "go_to_jail"
    assert by_position[38].amount == 100
    assert by_position[39].property_id == "property_boardwalk"

    properties_by_id = {property_data.id: property_data for property_data in data.properties}
    purchasable_spaces = [space for space in data.board if space.property_id is not None]
    assert len(purchasable_spaces) == 28
    for space in purchasable_spaces:
        assert space.property_id in properties_by_id
        assert properties_by_id[space.property_id].board_position == space.position


def test_properties_and_groups_are_complete_and_consistent() -> None:
    data = load_classic_monopoly_data()

    assert len(data.properties) == 28
    assert len({property_data.id for property_data in data.properties}) == 28

    streets = [property_data for property_data in data.properties if property_data.kind == "street"]
    railroads = [property_data for property_data in data.properties if property_data.kind == "railroad"]
    utilities = [property_data for property_data in data.properties if property_data.kind == "utility"]
    assert len(streets) == 22
    assert len(railroads) == 4
    assert len(utilities) == 2

    boardwalk = next(property_data for property_data in data.properties if property_data.id == "property_boardwalk")
    assert boardwalk.price == 400
    assert boardwalk.mortgage_value == 200
    assert boardwalk.rents == (50, 200, 600, 1400, 1700, 2000)
    assert boardwalk.house_cost == 200
    assert boardwalk.hotel_cost == 200

    reading = next(
        property_data for property_data in data.properties if property_data.id == "property_reading_railroad"
    )
    assert reading.price == 200
    assert reading.mortgage_value == 100
    assert reading.rent_by_owned_count == (25, 50, 100, 200)

    water_works = next(
        property_data for property_data in data.properties if property_data.id == "property_water_works"
    )
    assert water_works.price == 150
    assert water_works.mortgage_value == 75
    assert water_works.rent_multipliers == (4, 10)

    group_ids = {group.id for group in data.property_groups}
    assert group_ids == {
        "brown",
        "light_blue",
        "pink",
        "orange",
        "red",
        "yellow",
        "green",
        "dark_blue",
        "railroad",
        "utility",
    }

    properties_by_id = {property_data.id: property_data for property_data in data.properties}
    grouped_property_ids = {
        property_id for group in data.property_groups for property_id in group.property_ids
    }
    assert grouped_property_ids == set(properties_by_id)

    for group in data.property_groups:
        assert len(group.property_ids) in {2, 3, 4}
        for property_id in group.property_ids:
            property_data = properties_by_id[property_id]
            assert property_data.group == group.id
            if property_data.kind == "street":
                assert property_data.house_cost == group.house_cost
                assert property_data.hotel_cost == group.house_cost


def test_card_decks_have_classic_descriptor_coverage_and_unique_ids() -> None:
    data = load_classic_monopoly_data()

    assert len(data.decks.chance) == 16
    assert len(data.decks.community_chest) == 16

    for deck_name, cards in (
        ("chance", data.decks.chance),
        ("community_chest", data.decks.community_chest),
    ):
        assert len({card.id for card in cards}) == 16
        for card in cards:
            assert card.id.startswith("card_")
            assert card.deck == deck_name
            assert card.title
            assert card.description
            assert card.effect["type"]

    chance_effect_types = [card.effect["type"] for card in data.decks.chance]
    community_effect_types = [card.effect["type"] for card in data.decks.community_chest]
    assert chance_effect_types.count("advance_to_nearest_railroad") == 2
    assert "advance_to_nearest_utility" in chance_effect_types
    assert "building_repairs" in chance_effect_types
    assert "collect_from_each_player" in community_effect_types
    assert "building_repairs" in community_effect_types


def test_static_data_validation_rejects_invalid_references_and_inventory() -> None:
    data = load_classic_monopoly_data()
    payload = data.model_dump(mode="json")

    duplicate_position_payload = deepcopy(payload)
    duplicate_position_payload["board"][1]["position"] = 0
    with pytest.raises(ValidationError):
        ClassicMonopolyData.model_validate(duplicate_position_payload)

    missing_property_payload = deepcopy(payload)
    missing_property_payload["board"][1]["property_id"] = "property_missing"
    with pytest.raises(ValidationError):
        ClassicMonopolyData.model_validate(missing_property_payload)

    bad_inventory_payload = deepcopy(payload)
    bad_inventory_payload["bank_inventory"]["houses"] = 31
    with pytest.raises(ValidationError):
        ClassicMonopolyData.model_validate(bad_inventory_payload)
