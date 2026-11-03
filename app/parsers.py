"""Normalize platform payloads into one event shape.

kind is one of: received | acknowledged | failed | cancelled | other
Payload field names differ between platform API versions, so every lookup has fallbacks.
Check them against a real webhook sample from your account before going live.
"""
from dataclasses import dataclass

from .config import Settings


@dataclass
class OrderEvent:
    source: str
    order_id: str
    kind: str
    status: str = ""
    display_id: str | None = None
    channel: str | None = None
    store: str | None = None
    reason: str | None = None


def _first(d: dict, *paths):
    for path in paths:
        cur = d
        for key in path.split("."):
            if not isinstance(cur, dict) or key not in cur:
                cur = None
                break
            cur = cur[key]
        if cur not in (None, ""):
            return cur
    return None


def _classify(status: str, received, acked, failed, cancelled) -> str:
    s = status.upper()
    if s in failed:
        return "failed"
    if s in cancelled:
        return "cancelled"
    if s in acked:
        return "acknowledged"
    if s in received:
        return "received"
    return "other"


def parse_generic(p: dict) -> OrderEvent:
    """For your own integration system: {order_id, kind, status?, channel?, store?, reason?, display_id?, source?}"""
    kind = str(p.get("kind", "other")).lower()
    if kind not in {"received", "acknowledged", "failed", "cancelled", "other"}:
        raise ValueError(f"invalid kind: {kind}")
    if not p.get("order_id"):
        raise ValueError("order_id is required")
    return OrderEvent(
        source=str(p.get("source", "integration")),
        order_id=str(p["order_id"]),
        kind=kind,
        status=str(p.get("status", kind)),
        display_id=p.get("display_id"),
        channel=p.get("channel"),
        store=p.get("store"),
        reason=p.get("reason"),
    )


def parse_deliverect(p: dict, s: Settings) -> OrderEvent:
    order_id = _first(p, "_id", "orderId", "channelOrderId")
    if not order_id:
        raise ValueError("no order id in Deliverect payload")
    status = str(_first(p, "status") or "")
    return OrderEvent(
        source="deliverect",
        order_id=str(order_id),
        kind=_classify(status, s.deliverect_received, s.deliverect_acked, s.deliverect_failed, s.deliverect_cancelled),
        status=status,
        display_id=_first(p, "channelOrderDisplayId", "channelOrderId"),
        channel=str(_first(p, "channelName", "channel") or "") or None,
        store=_first(p, "locationName", "location.name", "location", "channelLink"),
        reason=_first(p, "failureReason", "reason", "note"),
    )


def parse_urbanpiper(p: dict, s: Settings) -> OrderEvent:
    order_id = _first(p, "order_id", "order.details.id", "order.details.ext_platforms.0.id")
    if not order_id:
        raise ValueError("no order id in UrbanPiper payload")
    state = _first(p, "new_state", "order.details.order_state", "order_state")
    # An order-placed webhook carries the full order but no state field.
    status = str(state or ("Placed" if "order" in p else ""))
    return OrderEvent(
        source="urbanpiper",
        order_id=str(order_id),
        kind=_classify(status, s.urbanpiper_received, s.urbanpiper_acked, s.urbanpiper_failed, s.urbanpiper_cancelled),
        status=status,
        display_id=_first(p, "order.details.ext_platforms.0.id"),
        channel=_first(p, "order.details.channel", "channel"),
        store=_first(p, "store_id", "order.store.name", "order.store.merchant_ref_id"),
        reason=_first(p, "message", "reason"),
    )
