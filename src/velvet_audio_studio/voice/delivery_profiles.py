from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeliveryProfile:
    profile_id: str
    length_scale: float
    volume: float
    noise_scale: float
    noise_w_scale: float
    normalize_audio: bool = True
    safety_locked: bool = False

    def __post_init__(self) -> None:
        if not self.profile_id.strip():
            raise ValueError("delivery profile_id must be non-empty")
        if not 0.75 <= self.length_scale <= 1.35:
            raise ValueError("delivery length_scale must be between 0.75 and 1.35")
        if not 0.35 <= self.volume <= 1.25:
            raise ValueError("delivery volume must be between 0.35 and 1.25")
        if not 0.0 <= self.noise_scale <= 1.0:
            raise ValueError("delivery noise_scale must be between 0 and 1")
        if not 0.0 <= self.noise_w_scale <= 1.0:
            raise ValueError("delivery noise_w_scale must be between 0 and 1")


@dataclass(frozen=True)
class DeliveryContext:
    requested_profile_id: str = "owner_default"
    severity: str = "informational"
    driving_load: str = "low"
    audience: str = "owner"
    quiet_requested: bool = False
    social_allowed: bool = False

    def __post_init__(self) -> None:
        requested = self.requested_profile_id.strip()
        severity = self.severity.strip().casefold()
        driving_load = self.driving_load.strip().casefold()
        audience = self.audience.strip().casefold()
        if not requested:
            raise ValueError("requested_profile_id must be non-empty")
        if severity not in {"casual", "informational", "warning", "critical", "emergency"}:
            raise ValueError("unsupported delivery severity")
        if driving_load not in {"low", "medium", "high"}:
            raise ValueError("driving_load must be low, medium, or high")
        if not audience:
            raise ValueError("audience must be non-empty")
        object.__setattr__(self, "requested_profile_id", requested)
        object.__setattr__(self, "severity", severity)
        object.__setattr__(self, "driving_load", driving_load)
        object.__setattr__(self, "audience", audience)


_PROFILES = {
    "owner_default": DeliveryProfile(
        profile_id="owner_default",
        length_scale=1.00,
        volume=1.00,
        noise_scale=0.667,
        noise_w_scale=0.80,
    ),
    "guest_reserved": DeliveryProfile(
        profile_id="guest_reserved",
        length_scale=1.03,
        volume=0.95,
        noise_scale=0.52,
        noise_w_scale=0.68,
    ),
    "high_driving_load": DeliveryProfile(
        profile_id="high_driving_load",
        length_scale=0.90,
        volume=1.05,
        noise_scale=0.42,
        noise_w_scale=0.52,
        safety_locked=True,
    ),
    "warning": DeliveryProfile(
        profile_id="warning",
        length_scale=0.88,
        volume=1.10,
        noise_scale=0.32,
        noise_w_scale=0.42,
        safety_locked=True,
    ),
    "emergency": DeliveryProfile(
        profile_id="emergency",
        length_scale=0.82,
        volume=1.15,
        noise_scale=0.18,
        noise_w_scale=0.28,
        safety_locked=True,
    ),
    "quiet_night": DeliveryProfile(
        profile_id="quiet_night",
        length_scale=1.06,
        volume=0.55,
        noise_scale=0.45,
        noise_w_scale=0.58,
    ),
    "playful_social": DeliveryProfile(
        profile_id="playful_social",
        length_scale=0.95,
        volume=0.98,
        noise_scale=0.82,
        noise_w_scale=0.92,
    ),
}


def delivery_profile(profile_id: str) -> DeliveryProfile:
    normalized = profile_id.strip()
    if not normalized:
        raise ValueError("delivery profile_id must be non-empty")
    try:
        return _PROFILES[normalized]
    except KeyError as exc:
        supported = ", ".join(sorted(_PROFILES))
        raise ValueError(
            f"unknown delivery profile {normalized!r}; supported profiles: {supported}"
        ) from exc


def select_delivery_profile(context: DeliveryContext) -> DeliveryProfile:
    """Resolve acoustic delivery while preventing style from weakening safety speech."""

    if context.severity == "emergency":
        return delivery_profile("emergency")
    if context.severity in {"warning", "critical"}:
        return delivery_profile("warning")
    if context.driving_load == "high":
        return delivery_profile("high_driving_load")
    if context.audience != "owner":
        return delivery_profile("guest_reserved")
    if context.quiet_requested:
        return delivery_profile("quiet_night")
    if context.requested_profile_id == "playful_social" and not context.social_allowed:
        return delivery_profile("owner_default")
    return delivery_profile(context.requested_profile_id)


def delivery_profile_ids() -> tuple[str, ...]:
    return tuple(_PROFILES)
