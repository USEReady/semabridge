"""
Unified relationship normalizer for Power BI / Fabric / TMSL / SML metadata.

Handles all relationship cardinality and cross-filtering formats seen in
either the JSON path (DataModelSchema / Fabric TMSL) or binary pbixray path:
- Cardinality:
    - fromCardinality / toCardinality pairs ("many" / "one", 1 / 2)
    - "cardinality" key: ManyToOne, OneToMany, OneToOne, ManyToMany
    - Shorthand: M:1, 1:M, 1:1, M:M
    - Hyphenated / delimited: many-to-one, one-to-many, etc.
    - pbixray numeric codes: FromCardinality / ToCardinality (1 = one, 2 = many)
- Cross-filtering:
    - Single, OneDirection, Both, BothDirections, Automatic
    - pbixray numeric codes (1 = OneDirection, 2 = BothDirections, 3 = Automatic)
- Metadata flags:
    - isActive / is_active
    - joinOnDateBehavior / join_on_date_behavior (1 -> dateAndTime, 2 -> dateOnly)
    - relyOnReferentialIntegrity / rely_on_referential_integrity (0/1 -> False/True)

Fails visibly on unknown or invalid values; documents and logs default once
only when a field is genuinely absent from the source format.
"""

from typing import Any, Dict, Optional, Set, Tuple
import logging

from semabridge.core.exceptions import ConversionError
from semabridge.intermediate.models import OSICardinality, OSICrossFilterDirection

logger = logging.getLogger(__name__)

# Track relationships that logged missing field defaults so we log once per relationship
_LOGGED_MISSING_CARDINALITY: Set[str] = set()
_LOGGED_MISSING_CROSS_FILTER: Set[str] = set()


def _get_key_case_insensitive(data: Dict[str, Any], *target_keys: str) -> Tuple[bool, Any]:
    """
    Check if any of target_keys exist in data (case-insensitively).
    Returns (key_present, value).
    """
    target_lower = {k.lower(): k for k in target_keys}
    for k, v in data.items():
        if k.lower() in target_lower:
            return True, v
    return False, None


def _normalize_endpoint_cardinality(val: Any) -> Optional[str]:
    """Normalize a single endpoint cardinality to 'one' or 'many'."""
    if val is None:
        return None
    if isinstance(val, int):
        if val == 1:
            return "one"
        if val == 2:
            return "many"
        return None
    s = str(val).strip().lower()
    if s in ("1", "one", "single"):
        return "one"
    if s in ("2", "many", "m"):
        return "many"
    return None


def normalize_cardinality(rel_def: Dict[str, Any], rel_name: str = "") -> OSICardinality:
    """
    Normalize cardinality from any supported format into OSICardinality.

    Supported formats:
    - fromCardinality / toCardinality pairs ("many"/"one", numeric 1/2)
    - "cardinality" key: ManyToOne, OneToMany, OneToOne, ManyToMany
    - Shorthand: M:1, 1:M, 1:1, M:M
    - Hyphenated / delimited: many-to-one, one-to-many, etc.

    Raises:
        ConversionError: If a cardinality field is present but has an unknown,
                         invalid, or incomplete value.
    """
    # Check for fromCardinality / toCardinality
    has_from, from_val = _get_key_case_insensitive(rel_def, "fromCardinality", "from_cardinality")
    has_to, to_val = _get_key_case_insensitive(rel_def, "toCardinality", "to_cardinality")

    # Check for cardinality key
    has_card, card_val = _get_key_case_insensitive(rel_def, "cardinality")

    # If neither format's keys are present at all: genuinely absent
    if not has_from and not has_to and not has_card:
        if rel_name not in _LOGGED_MISSING_CARDINALITY:
            logger.warning(
                "Relationship '%s' genuinely has no cardinality field in source format; "
                "defaulting to documented default '%s'",
                rel_name,
                OSICardinality.MANY_TO_ONE.value,
            )
            _LOGGED_MISSING_CARDINALITY.add(rel_name)
        return OSICardinality.MANY_TO_ONE

    # If from/to pair is partially or fully specified
    if has_from or has_to:
        if not has_from or from_val is None or str(from_val).strip() == "":
            raise ConversionError(
                f"Relationship '{rel_name}' specifies toCardinality={to_val!r} but fromCardinality is missing",
                source_format="relationship_metadata",
                target_format="osi",
                details={"rel_name": rel_name, "from_val": from_val, "to_val": to_val},
            )
        if not has_to or to_val is None or str(to_val).strip() == "":
            raise ConversionError(
                f"Relationship '{rel_name}' specifies fromCardinality={from_val!r} but toCardinality is missing",
                source_format="relationship_metadata",
                target_format="osi",
                details={"rel_name": rel_name, "from_val": from_val, "to_val": to_val},
            )

        norm_from = _normalize_endpoint_cardinality(from_val)
        norm_to = _normalize_endpoint_cardinality(to_val)

        if norm_from is None or norm_to is None:
            raise ConversionError(
                f"Invalid or unrecognized endpoint cardinality in relationship '{rel_name}': "
                f"fromCardinality={from_val!r}, toCardinality={to_val!r}",
                source_format="relationship_metadata",
                target_format="osi",
                details={"rel_name": rel_name, "from_val": from_val, "to_val": to_val},
            )

        pair_map = {
            ("many", "one"): OSICardinality.MANY_TO_ONE,
            ("one", "many"): OSICardinality.ONE_TO_MANY,
            ("one", "one"): OSICardinality.ONE_TO_ONE,
            ("many", "many"): OSICardinality.MANY_TO_MANY,
        }
        return pair_map[(norm_from, norm_to)]

    # If cardinality key is specified
    if has_card:
        if card_val is None or (isinstance(card_val, str) and not card_val.strip()):
            raise ConversionError(
                f"Relationship '{rel_name}' has an empty or null cardinality value",
                source_format="relationship_metadata",
                target_format="osi",
                details={"rel_name": rel_name, "card_val": card_val},
            )

        if isinstance(card_val, OSICardinality):
            return card_val

        card_str = str(card_val).strip().lower().replace("_", "").replace("-", "").replace(" ", "")

        # Lookups for shorthand, concatenated, and standard names
        card_lookup = {
            "manytoone": OSICardinality.MANY_TO_ONE,
            "m:1": OSICardinality.MANY_TO_ONE,
            "m1": OSICardinality.MANY_TO_ONE,
            "m/1": OSICardinality.MANY_TO_ONE,
            "onetomany": OSICardinality.ONE_TO_MANY,
            "1:m": OSICardinality.ONE_TO_MANY,
            "1m": OSICardinality.ONE_TO_MANY,
            "1/m": OSICardinality.ONE_TO_MANY,
            "onetoone": OSICardinality.ONE_TO_ONE,
            "1:1": OSICardinality.ONE_TO_ONE,
            "11": OSICardinality.ONE_TO_ONE,
            "1/1": OSICardinality.ONE_TO_ONE,
            "manytomany": OSICardinality.MANY_TO_MANY,
            "m:m": OSICardinality.MANY_TO_MANY,
            "mm": OSICardinality.MANY_TO_MANY,
            "m/m": OSICardinality.MANY_TO_MANY,
        }

        if card_str in card_lookup:
            return card_lookup[card_str]

        raise ConversionError(
            f"Invalid or unrecognized cardinality '{card_val}' in relationship '{rel_name}'",
            source_format="relationship_metadata",
            target_format="osi",
            details={"rel_name": rel_name, "card_val": card_val},
        )

    # Fallback default
    return OSICardinality.MANY_TO_ONE


def normalize_cross_filter(rel_def: Dict[str, Any], rel_name: str = "") -> OSICrossFilterDirection:
    """
    Normalize cross-filtering behavior from any supported format into OSICrossFilterDirection.

    Supported formats:
    - String values: Single, OneDirection, Both, BothDirections, Automatic
    - Numeric codes: 1 = OneDirection/Single, 2 = BothDirections/Both, 3 = Automatic

    Raises:
        ConversionError: If a cross-filtering field is present but has an unknown,
                         invalid, or empty value.
    """
    has_cf, cf_val = _get_key_case_insensitive(
        rel_def,
        "crossFilteringBehavior",
        "cross_filtering_behavior",
        "crossFilterDirection",
        "cross_filter",
    )

    if not has_cf:
        if rel_name not in _LOGGED_MISSING_CROSS_FILTER:
            logger.warning(
                "Relationship '%s' genuinely has no cross-filter field in source format; "
                "defaulting to documented default '%s'",
                rel_name,
                OSICrossFilterDirection.SINGLE.value,
            )
            _LOGGED_MISSING_CROSS_FILTER.add(rel_name)
        return OSICrossFilterDirection.SINGLE

    if cf_val is None or (isinstance(cf_val, str) and not cf_val.strip()):
        raise ConversionError(
            f"Relationship '{rel_name}' has an empty or null crossFilteringBehavior value",
            source_format="relationship_metadata",
            target_format="osi",
            details={"rel_name": rel_name, "cf_val": cf_val},
        )

    if isinstance(cf_val, OSICrossFilterDirection):
        return cf_val

    # Numeric codes (1 = OneDirection, 2 = BothDirections, 3 = Automatic)
    if isinstance(cf_val, int) and not isinstance(cf_val, bool):
        if cf_val == 1:
            return OSICrossFilterDirection.SINGLE
        if cf_val == 2:
            return OSICrossFilterDirection.BOTH
        if cf_val == 3:
            return OSICrossFilterDirection.AUTOMATIC
        raise ConversionError(
            f"Invalid or unrecognized cross-filtering numeric code '{cf_val}' in relationship '{rel_name}'",
            source_format="relationship_metadata",
            target_format="osi",
            details={"rel_name": rel_name, "cf_val": cf_val},
        )

    cf_str = str(cf_val).strip().lower().replace("_", "")

    cf_lookup = {
        "single": OSICrossFilterDirection.SINGLE,
        "onedirection": OSICrossFilterDirection.SINGLE,
        "1": OSICrossFilterDirection.SINGLE,
        "both": OSICrossFilterDirection.BOTH,
        "bothdirections": OSICrossFilterDirection.BOTH,
        "2": OSICrossFilterDirection.BOTH,
        "automatic": OSICrossFilterDirection.AUTOMATIC,
        "auto": OSICrossFilterDirection.AUTOMATIC,
        "3": OSICrossFilterDirection.AUTOMATIC,
    }

    if cf_str in cf_lookup:
        return cf_lookup[cf_str]

    raise ConversionError(
        f"Invalid or unrecognized cross-filtering behavior '{cf_val}' in relationship '{rel_name}'",
        source_format="relationship_metadata",
        target_format="osi",
        details={"rel_name": rel_name, "cf_val": cf_val},
    )


def normalize_is_active(rel_def: Dict[str, Any], default: bool = True) -> bool:
    """Normalize isActive / is_active flag."""
    has_active, active_val = _get_key_case_insensitive(rel_def, "isActive", "is_active")
    if not has_active or active_val is None:
        return default
    if isinstance(active_val, bool):
        return active_val
    if isinstance(active_val, (int, float)):
        return bool(active_val)
    s = str(active_val).strip().lower()
    if s in ("true", "1", "yes"):
        return True
    if s in ("false", "0", "no"):
        return False
    return bool(s)


def normalize_join_on_date_behavior(rel_def: Dict[str, Any]) -> Optional[str]:
    """
    Normalize joinOnDateBehavior.
    Maps numeric 1 -> 'dateAndTime', 2 -> 'dateOnly', or preserves string.
    """
    has_key, val = _get_key_case_insensitive(rel_def, "joinOnDateBehavior", "join_on_date_behavior")
    if not has_key or val is None:
        return None
    if isinstance(val, int):
        if val == 1:
            return "dateAndTime"
        if val == 2:
            return "dateOnly"
        return str(val)
    s = str(val).strip()
    if s == "1":
        return "dateAndTime"
    if s == "2":
        return "dateOnly"
    return s if s else None


def normalize_rely_on_referential_integrity(rel_def: Dict[str, Any]) -> Optional[bool]:
    """
    Normalize relyOnReferentialIntegrity flag.
    Maps 0/1 or bool to Optional[bool].
    """
    has_key, val = _get_key_case_insensitive(
        rel_def, "relyOnReferentialIntegrity", "rely_on_referential_integrity"
    )
    if not has_key or val is None:
        return None
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    s = str(val).strip().lower()
    if s in ("true", "1"):
        return True
    if s in ("false", "0"):
        return False
    return bool(s)
