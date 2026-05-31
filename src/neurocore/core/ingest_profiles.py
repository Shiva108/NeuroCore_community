"""Shared ingest-profile validation and resolution helpers."""

from __future__ import annotations

import re

SUPPORTED_SOURCES = {"slack", "discord"}
SUPPORTED_MATCH_FIELDS = {
    "team_id",
    "channel_id",
    "guild_id",
    "author_id",
    "user_id",
}
BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
VALID_SENSITIVITIES = {"standard", "restricted", "sealed"}


def validate_ingest_profiles(
    payload: object, *, allowed_buckets: tuple[str, ...]
) -> dict[str, object]:
    """Validate a JSON ingest-profile document and return a normalized payload."""
    if not isinstance(payload, dict):
        raise ValueError("ingest profile document must be an object")
    version = str(payload.get("version", "")).strip()
    if not version:
        raise ValueError("ingest profile document must include a version")
    raw_profiles = payload.get("profiles")
    if not isinstance(raw_profiles, list):
        raise ValueError("ingest profile document must include a profiles list")

    profiles: list[dict[str, object]] = []
    for index, raw_profile in enumerate(raw_profiles):
        if not isinstance(raw_profile, dict):
            raise ValueError(f"profile #{index + 1} must be an object")
        name = str(raw_profile.get("name", "")).strip()
        if not name:
            raise ValueError(f"profile #{index + 1} must include a name")
        source = str(raw_profile.get("source", "")).strip().lower()
        if source not in SUPPORTED_SOURCES:
            raise ValueError(
                f"profile {name} must use one of: {', '.join(sorted(SUPPORTED_SOURCES))}"
            )

        match = raw_profile.get("match")
        if not isinstance(match, dict) or not match:
            raise ValueError(f"profile {name} must include a non-empty match object")
        invalid_keys = sorted(set(match) - SUPPORTED_MATCH_FIELDS)
        if invalid_keys:
            raise ValueError(
                f"profile {name} has unsupported match fields: {', '.join(invalid_keys)}"
            )
        normalized_match = {
            str(key): str(value).strip()
            for key, value in match.items()
            if str(value).strip()
        }
        if not normalized_match:
            raise ValueError(f"profile {name} must include non-empty match values")

        defaults = raw_profile.get("defaults")
        if not isinstance(defaults, dict):
            raise ValueError(f"profile {name} must include a defaults object")
        normalized_defaults: dict[str, object] = {}
        if "bucket" in defaults:
            bucket = str(defaults["bucket"]).strip()
            if not BUCKET_PATTERN.match(bucket):
                raise ValueError(f"profile {name} bucket must be valid")
            if bucket not in allowed_buckets:
                raise ValueError(
                    f"profile {name} bucket must be one of: {', '.join(allowed_buckets)}"
                )
            normalized_defaults["bucket"] = bucket
        if "sensitivity" in defaults:
            sensitivity = str(defaults["sensitivity"]).strip().lower()
            if sensitivity not in VALID_SENSITIVITIES:
                raise ValueError(
                    f"profile {name} sensitivity must be one of: {', '.join(sorted(VALID_SENSITIVITIES))}"
                )
            normalized_defaults["sensitivity"] = sensitivity
        if "tags" in defaults:
            tags = defaults["tags"]
            if not isinstance(tags, list) or not all(
                isinstance(tag, str) and tag.strip() for tag in tags
            ):
                raise ValueError(f"profile {name} tags must be a list of strings")
            normalized_defaults["tags"] = list(
                dict.fromkeys(tag.strip() for tag in tags)
            )

        parsing_hints = raw_profile.get("parsing_hints", {})
        if not isinstance(parsing_hints, dict):
            raise ValueError(f"profile {name} parsing_hints must be an object")

        profiles.append(
            {
                "name": name,
                "source": source,
                "match": normalized_match,
                "defaults": normalized_defaults,
                "parsing_hints": parsing_hints,
            }
        )

    return {"version": version, "profiles": profiles}


def resolve_ingest_profile(
    *,
    source: str,
    context: dict[str, object],
    configured_profiles: dict[str, object] | None,
) -> dict[str, object] | None:
    """Return the most specific matching profile for the given ingest context."""
    if not configured_profiles:
        return None
    raw_profiles = configured_profiles.get("profiles", [])
    if not isinstance(raw_profiles, list):
        return None

    best_match: dict[str, object] | None = None
    best_specificity = -1
    for profile in raw_profiles:
        if profile.get("source") != source:
            continue
        match = profile.get("match", {})
        if not isinstance(match, dict):
            continue
        if not all(
            str(context.get(key, "")).strip() == value for key, value in match.items()
        ):
            continue
        specificity = len(match)
        if specificity > best_specificity:
            best_match = profile
            best_specificity = specificity
    return best_match
