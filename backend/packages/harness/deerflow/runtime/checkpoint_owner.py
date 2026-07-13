"""Owner-qualified LangGraph checkpoint configurations.

The physical saver key is deliberately distinct from DeerFlow's external
thread id, which remains the API/event/runtime-context identity.
"""

from __future__ import annotations


def owner_checkpoint_thread_id(thread_id: str, owner_user_id: str) -> str:
    """Return a stable versioned, length-delimited physical saver key."""
    external = str(thread_id).encode("utf-8")
    owner = str(owner_user_id).encode("utf-8")
    return f"dfcp1:{len(owner)}:{owner.hex()}:{len(external)}:{external.hex()}"


def owner_checkpoint_config(thread_id: str, owner_user_id: str, *, checkpoint_ns: str | None = None, checkpoint_id: str | None = None) -> dict:
    """Build saver config from a server-derived owner and external thread id."""
    configurable: dict[str, str] = {
        "thread_id": owner_checkpoint_thread_id(thread_id, owner_user_id),
        "user_id": str(owner_user_id),
    }
    if checkpoint_ns is not None:
        configurable["checkpoint_ns"] = checkpoint_ns
    if checkpoint_id is not None:
        configurable["checkpoint_id"] = checkpoint_id
    return {"configurable": configurable}
