"use client";

import { useRouter, usePathname } from "next/navigation";
import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  useRef,
  type ReactNode,
} from "react";

import {
  fetch as fetcher,
  isUnauthorizedError,
  UNAUTHORIZED_EVENT,
} from "../api/fetcher";
import { getBackendBaseURL } from "../config";
import { isStaticWebsiteOnly } from "../static-mode";

import { type User, buildLoginUrl } from "./types";

// Re-export for consumers
export type { User };

/**
 * Authentication context provided to consuming components
 */
interface AuthContextType {
  user: User | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
  applyUser: (user: User | null) => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export function clearStreamReconnectKeys(storage?: Storage): void {
  try {
    const target =
      storage ??
      (typeof window !== "undefined" ? window.sessionStorage : undefined);
    if (!target) return;
    const keys: string[] = [];
    for (let i = 0; i < target.length; i += 1) {
      const key = target.key(i);
      if (key?.startsWith("lg:stream:")) keys.push(key);
    }
    for (const key of keys) target.removeItem(key);
  } catch {
    // Authentication state changes must not depend on storage availability.
  }
}

interface AuthProviderProps {
  children: ReactNode;
  initialUser: User | null;
}

/**
 * AuthProvider - Unified authentication context for the application
 *
 * Per RFC-001:
 * - Only holds display information (user), never JWT or tokens
 * - initialUser comes from server-side guard, avoiding client flicker
 * - Provides logout and refresh capabilities
 */
export function AuthProvider({ children, initialUser }: AuthProviderProps) {
  const [user, setUser] = useState<User | null>(initialUser);
  const [isLoading, setIsLoading] = useState(false);
  const router = useRouter();
  const pathname = usePathname();
  const staticMode = isStaticWebsiteOnly();
  const handlingUnauthorizedRef = useRef(false);
  const refreshInFlightRef = useRef<Promise<void> | null>(null);

  const isAuthenticated = user !== null;

  /**
   * Apply a user value supplied by a caller (e.g. banner probe) that has
   * already fetched it. Equivalent to setUser, exposed with a stable name
   * so consumers don't reach into React internals.
   */
  const applyUser = useCallback((next: User | null) => {
    if (next) handlingUnauthorizedRef.current = false;
    setUser((prev) => {
      if (prev?.id !== next?.id || prev?.email !== next?.email) {
        clearStreamReconnectKeys();
      }
      return next;
    });
  }, []);

  const handleUnauthorized = useCallback(() => {
    if (handlingUnauthorizedRef.current) return;
    handlingUnauthorizedRef.current = true;
    clearStreamReconnectKeys();
    setUser(null);
    if (pathname?.startsWith("/workspace")) {
      router.push(buildLoginUrl(pathname));
    }
  }, [pathname, router]);

  useEffect(() => {
    window.addEventListener(UNAUTHORIZED_EVENT, handleUnauthorized);
    return () =>
      window.removeEventListener(UNAUTHORIZED_EVENT, handleUnauthorized);
  }, [handleUnauthorized]);

  /**
   * Fetch current user from FastAPI
   * Used when initialUser might be stale (e.g., after tab was inactive)
   */
  const refreshUser = useCallback(async () => {
    if (staticMode) return;
    if (refreshInFlightRef.current) {
      await refreshInFlightRef.current;
      return;
    }

    const request = (async () => {
      try {
        setIsLoading(true);
        const res = await fetcher(`${getBackendBaseURL()}/api/v1/auth/me`);

        if (res.ok) {
          const data = await res.json();
          handlingUnauthorizedRef.current = false;
          applyUser(data);
        } else if (res.status === 401) {
          // Session expired or invalid
          applyUser(null);
          // Redirect to login if on a protected route
          if (pathname?.startsWith("/workspace")) {
            router.push(buildLoginUrl(pathname));
          }
        }
      } catch (err) {
        if (isUnauthorizedError(err)) {
          return;
        }
        console.error("Failed to refresh user:", err);
      } finally {
        setIsLoading(false);
      }
    })();
    refreshInFlightRef.current = request;
    try {
      await request;
    } finally {
      if (refreshInFlightRef.current === request) {
        refreshInFlightRef.current = null;
      }
    }
  }, [applyUser, staticMode, pathname, router]);

  useEffect(() => {
    if (staticMode) return;
    const refreshIfVisible = () => {
      if (document.visibilityState === "visible") {
        void refreshUser();
      }
    };
    window.addEventListener("focus", refreshIfVisible);
    document.addEventListener("visibilitychange", refreshIfVisible);
    return () => {
      window.removeEventListener("focus", refreshIfVisible);
      document.removeEventListener("visibilitychange", refreshIfVisible);
    };
  }, [refreshUser, staticMode]);

  /**
   * Logout - call FastAPI logout endpoint and clear local state.
   * Per RFC-001: Immediately clear local state, don't wait for server confirmation.
   */
  const logout = useCallback(async () => {
    // Immediately clear local state and reconnect cursors to prevent UI flicker
    clearStreamReconnectKeys();
    setUser(null);

    if (staticMode) {
      router.push("/");
      return;
    }

    try {
      await fetcher(`${getBackendBaseURL()}/api/v1/auth/logout`, {
        method: "POST",
      });
    } catch (err) {
      console.error("Logout request failed:", err);
    }

    router.push("/");
  }, [staticMode, router]);

  const value: AuthContextType = {
    user,
    isAuthenticated,
    isLoading,
    logout,
    refreshUser,
    applyUser,
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/**
 * Hook to access authentication context
 * Throws if used outside AuthProvider - this is intentional for proper usage
 */
export function useAuth(): AuthContextType {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}

/**
 * Hook to require authentication - redirects to login if not authenticated
 * Useful for client-side checks in addition to server-side guards
 */
export function useRequireAuth(): AuthContextType {
  const auth = useAuth();
  const router = useRouter();
  const pathname = usePathname();

  useEffect(() => {
    if (isStaticWebsiteOnly()) return;

    // Only redirect if we're sure user is not authenticated (not just loading)
    if (!auth.isLoading && !auth.isAuthenticated) {
      router.push(buildLoginUrl(pathname || "/workspace"));
    }
  }, [auth.isAuthenticated, auth.isLoading, router, pathname]);

  return auth;
}
