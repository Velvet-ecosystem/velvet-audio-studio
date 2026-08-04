"""Deterministic ALSA card discovery for the Velvet audio node.

The studio never assumes that the Octo is card 0. USB devices, HDMI, and the
Pi's built-in audio can change ALSA card numbering between boots.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

_CARD_LINE = re.compile(r"^\s*(?P<index>\d+)\s+\[(?P<id>[^]]+)\]\s*:\s*(?P<name>.+)$")


@dataclass(frozen=True, slots=True)
class AlsaCard:
    index: int
    card_id: str
    name: str

    @property
    def hw_name(self) -> str:
        return f"hw:{self.index}"


def parse_proc_asound_cards(text: str) -> tuple[AlsaCard, ...]:
    """Parse ``/proc/asound/cards`` without relying on localized CLI output."""
    cards: list[AlsaCard] = []
    for line in text.splitlines():
        match = _CARD_LINE.match(line)
        if not match:
            continue
        cards.append(
            AlsaCard(
                index=int(match.group("index")),
                card_id=match.group("id").strip(),
                name=match.group("name").strip(),
            )
        )
    return tuple(cards)


def list_alsa_cards(path: Path = Path("/proc/asound/cards")) -> tuple[AlsaCard, ...]:
    if not path.exists():
        return ()
    return parse_proc_asound_cards(path.read_text(encoding="utf-8", errors="replace"))


def find_card(
    cards: tuple[AlsaCard, ...],
    *,
    identity_terms: tuple[str, ...] = ("audioinjector", "octo"),
) -> AlsaCard | None:
    """Return the first card whose stable identity contains every search term."""
    normalized_terms = tuple(term.casefold() for term in identity_terms)
    for card in cards:
        haystack = f"{card.card_id} {card.name}".casefold()
        if all(term in haystack for term in normalized_terms):
            return card
    return None
