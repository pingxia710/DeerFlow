"use client";

import { useEffect, useRef } from "react";

import { useAuth } from "@/core/auth/AuthProvider";
import { userSchema, type User } from "@/core/auth/types";
import { getBackendBaseURL } from "@/core/config";
import { useI18n } from "@/core/i18n/hooks";

import {
  classifyProbe,
  decideProbeAction,
  shouldShowOfflineBanner,
} from "./gateway-offline-banner-helpers";

interface GatewayOfflineBannerProps {
  /**
   * True when the server-side auth probe at `/api/v1/auth/me` could not
   * reach the gateway. The banner stays mounted until a client-side probe
   * confirms the gateway is healthy and `user` becomes populated.
   */
  gatewayUnavailable: boolean;
}

export function GatewayOfflineBanner({
  gatewayUnavailable,
}: GatewayOfflineBannerProps) {
  const { t } = useI18n();
  const { user, applyUser, logout } = useAuth();
  // Guard against piling up probe calls while the gateway is still slow.
  const inFlightRef = useRef(false);

  useEffect(() => {
    if (!gatewayUnavailable) return;
    // Once AuthProvider has a user again the banner has served its purpose.
    // gatewayUnavailable is a server-rendered prop and stays true until a full reload.
    if (user !== null) return;

    const probe = async () => {
      if (inFlightRef.current) return;
      inFlightRef.current = true;
      let res: Response | null = null;
      let errored = false;
      let parsedUser: User | null = null;
      try {
        res = await fetch(`${getBackendBaseURL()}/api/v1/auth/me`, {
          credentials: "include",
          cache: "no-store",
        });
        // Reuse the probe's own response body instead of triggering another
        // /auth/me request.
        if (res.ok) {
          try {
            const data = await res.json();
            const parsed = userSchema.safeParse(data);
            if (parsed.success) parsedUser = parsed.data;
          } catch (err) {
            console.warn(
              "[gateway-offline-banner] probe body parse failed:",
              err,
            );
          }
        }
      } catch (err) {
        console.warn("[gateway-offline-banner] probe failed:", err);
        errored = true;
      } finally {
        inFlightRef.current = false;
      }

      const action = decideProbeAction(classifyProbe(res, errored, parsedUser));

      if (action.type === "apply-user") {
        applyUser(action.user);
      }
    };

    void probe();
  }, [gatewayUnavailable, user, applyUser]);

  if (!shouldShowOfflineBanner(user, gatewayUnavailable)) {
    return null;
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className="bg-muted text-muted-foreground flex items-center justify-between gap-3 border-b px-4 py-2 text-sm"
    >
      <span>
        {t.workspace.gatewayUnavailable} {t.workspace.gatewayUnavailableHelp}
      </span>
      <button
        type="button"
        onClick={() => {
          void logout();
        }}
        className="hover:bg-background rounded-md border px-3 py-1 text-xs"
      >
        {t.workspace.logout}
      </button>
    </div>
  );
}
