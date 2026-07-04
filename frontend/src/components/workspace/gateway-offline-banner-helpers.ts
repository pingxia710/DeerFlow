import type { User } from "@/core/auth/types";

export function shouldShowOfflineBanner(
  user: User | null,
  gatewayUnavailable: boolean,
): boolean {
  return gatewayUnavailable && user === null;
}

/** Categorised outcome of a single /auth/me probe. */
export type ProbeOutcome =
  | { kind: "ok"; user: User } // 2xx with parsed body
  | { kind: "unauthorized" } // 401
  | { kind: "transient" }; // 5xx, network, abort, malformed body, etc.

/** Next action the banner effect should take after a probe. */
export type ProbeAction = { type: "apply-user"; user: User } | { type: "noop" };

/**
 * Pure: classify an HTTP probe outcome into ProbeOutcome.
 *
 * Extracted from the banner effect so it can be unit-tested independently.
 * `parsedUser` is the JSON body of a 2xx response (or null if absent/malformed);
 * surfacing it through ProbeOutcome lets the caller apply it directly.
 */
export function classifyProbe(
  res: Response | null,
  errored: boolean,
  parsedUser: User | null = null,
): ProbeOutcome {
  if (errored || res === null) return { kind: "transient" };
  if (res.ok && parsedUser !== null) return { kind: "ok", user: parsedUser };
  if (res.ok) return { kind: "transient" }; // 2xx but body unusable
  if (res.status === 401) return { kind: "unauthorized" };
  return { kind: "transient" };
}

/**
 * Pure state machine for what to do after a probe lands.
 *
 * Outputs: either "apply the user body we just fetched" or "do nothing".
 */
export function decideProbeAction(outcome: ProbeOutcome): ProbeAction {
  if (outcome.kind === "ok") {
    return { type: "apply-user", user: outcome.user };
  }
  return { type: "noop" };
}
