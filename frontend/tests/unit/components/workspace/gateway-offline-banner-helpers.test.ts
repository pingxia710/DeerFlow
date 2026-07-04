import { describe, expect, it } from "@rstest/core";

import {
  classifyProbe,
  decideProbeAction,
  shouldShowOfflineBanner,
} from "@/components/workspace/gateway-offline-banner-helpers";
import type { User } from "@/core/auth/types";

const fakeUser: User = {
  id: "u1",
  email: "user@example.com",
  system_role: "user",
  needs_setup: false,
};

function makeResponse(status: number, ok = status >= 200 && status < 300) {
  return { status, ok } as Response;
}

describe("shouldShowOfflineBanner", () => {
  it("hides when the gateway is reachable", () => {
    expect(shouldShowOfflineBanner(null, false)).toBe(false);
    expect(shouldShowOfflineBanner(fakeUser, false)).toBe(false);
  });

  it("shows when the gateway is unavailable and the client has no user yet", () => {
    expect(shouldShowOfflineBanner(null, true)).toBe(true);
  });

  it("hides as soon as the client recovers an authenticated user", () => {
    expect(shouldShowOfflineBanner(fakeUser, true)).toBe(false);
  });
});

describe("classifyProbe", () => {
  it("returns transient when fetch errored", () => {
    expect(classifyProbe(null, true)).toEqual({ kind: "transient" });
  });

  it("returns transient when response is null with no error flag", () => {
    expect(classifyProbe(null, false)).toEqual({ kind: "transient" });
  });

  it("returns ok with parsed user for a 2xx response with body", () => {
    expect(classifyProbe(makeResponse(200), false, fakeUser)).toEqual({
      kind: "ok",
      user: fakeUser,
    });
  });

  it("returns transient for a 2xx response whose body failed to parse", () => {
    // Defensive: a 200 with malformed JSON / schema mismatch should not be
    // treated as 'ok' because the caller has no user to apply.
    expect(classifyProbe(makeResponse(200), false, null)).toEqual({
      kind: "transient",
    });
  });

  it("returns unauthorized for a 401 response", () => {
    expect(classifyProbe(makeResponse(401), false)).toEqual({
      kind: "unauthorized",
    });
  });

  it("returns transient for 5xx responses", () => {
    expect(classifyProbe(makeResponse(503), false)).toEqual({
      kind: "transient",
    });
    expect(classifyProbe(makeResponse(500), false)).toEqual({
      kind: "transient",
    });
  });

  it("returns transient for unexpected non-401 4xx responses", () => {
    expect(classifyProbe(makeResponse(429), false)).toEqual({
      kind: "transient",
    });
  });
});

describe("decideProbeAction", () => {
  it("returns apply-user with the body on a 2xx response", () => {
    expect(decideProbeAction({ kind: "ok", user: fakeUser })).toEqual({
      type: "apply-user",
      user: fakeUser,
    });
  });

  it("does nothing for 401 and transient outcomes", () => {
    expect(decideProbeAction({ kind: "unauthorized" })).toEqual({
      type: "noop",
    });
    expect(decideProbeAction({ kind: "transient" })).toEqual({
      type: "noop",
    });
  });
});
