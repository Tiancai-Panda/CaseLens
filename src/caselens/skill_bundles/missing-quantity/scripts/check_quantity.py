"""Trusted deterministic reconciliation, without model or filesystem access."""


def check_quantity(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Quantity input must be a mapping")
    allowed = {"expected", "received", "pending", "order_evidence", "complete_photo", "evidence_ids"}
    if set(data) - allowed:
        raise ValueError("Unknown quantity input field")
    counts = {}
    for field in ("expected", "received", "pending"):
        value = data.get(field)
        if value is not None and (type(value) is not int or not 0 <= value <= 1_000_000):
            raise ValueError(f"{field} must be a nonnegative bounded integer or null")
        counts[field] = value
    for flag in ("order_evidence", "complete_photo"):
        if type(data.get(flag, False)) is not bool:
            raise ValueError(f"{flag} must be a boolean")
    ids = data.get("evidence_ids", [])
    if not isinstance(ids, list) or len(ids) > 64 or any(
        not isinstance(item, str) or not item.strip() or len(item) > 128 for item in ids
    ) or len(set(ids)) != len(ids):
        raise ValueError("Evidence IDs must be unique bounded nonempty strings")
    expected, received, pending = (counts[key] for key in ("expected", "received", "pending"))
    missing = (max(0, expected - received - pending)
               if all(value is not None for value in counts.values()) else None)
    enough = (
        data.get("order_evidence", False) and data.get("complete_photo", False) and bool(ids)
        and all(value is not None for value in counts.values())
    )
    if not enough:
        status = "missing_evidence"
        reason = "Need verified order quantity, complete receipt evidence and resolved shipment status."
    elif received + pending > expected:
        status = "conflict"
        reason = "Received and pending counts exceed order quantity; resolve conflicting evidence."
    elif pending > 0:
        status = "pending_shipment"
        reason = "Items remain in transit; reconcile all packages before deciding shortage."
    elif missing > 0:
        status = "shortage"
        reason = "Complete order and receipt evidence indicate a shortage after all packages arrived."
    else:
        status = "consistent"
        reason = "Received quantity matches the order with no pending items."
    return {"status": status, "missing_count": missing, "reason": reason, "evidence_ids": list(ids)}
