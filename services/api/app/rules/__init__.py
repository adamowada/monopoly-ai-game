"""Rules package exports for static Monopoly domain data."""

from app.rules.static_data import (
    BankInventory,
    BoardSpace,
    CardData,
    ClassicMonopolyData,
    Currency,
    Decks,
    PropertyData,
    PropertyGroup,
    load_classic_monopoly_data,
)

__all__ = [
    "BankInventory",
    "BoardSpace",
    "CardData",
    "ClassicMonopolyData",
    "Currency",
    "Decks",
    "PropertyData",
    "PropertyGroup",
    "load_classic_monopoly_data",
]
