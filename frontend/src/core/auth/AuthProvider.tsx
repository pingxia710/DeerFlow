"use client";

import { useRouter, usePathname } from "next/navigation";
import {
  createContext,
  useContext,
  useState,
  useCallback,
  useEffect,
  type ReactNode,
} from "react";

import { fetch as fetcher } from "../api/fetcher";
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

function clearStreamReconnectKeys(
  storage: Storage | undefined = typeof window !== "undefined"
    ? window.sessionStorage
    : undefined,
): void {
  if (!storage) return;
  const keys: string[] = [];
  for (let i = 0; i < storage.length; i += 1) {
    const key = storage.key(i);
    if (key?.startsWith("lg:stream:")) keys.push(key);
  }
  for (const key of keys) storage.removeItem(key);
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

  const isAuthenticated = user !== null;

  /**
   * Apply a user value supplied by a caller (e.g. banner probe) that has
   * already fetched it. Equivalent to setUser, exposed with a stable name
   * so consumers don't reach into React internals.
   */
  const applyUser = useCallback((next: User | null) => {
    setUser((prev) => {
      if (prev?.id !== next?.id || prev?.email !== next?.email) {
        clearStreamReconnectKeys();
      }
      return next;
    });
  }, []);

  /**
   * Fetch current user from FastAPI
   * Used when initialUser might be stale (e.g., after tab was inactive)
   */
  const refreshUser = useCallback(async () => {
    if (staticMode) return;

    try {
      setIsLoading(true);
      const res = await fetcher("/api/v1/auth/me");

      if (res.ok) {
        const data = await res.json();
        setUser(data);
      } else if (res.status === 401) {
        // Session expired or invalid
        setUser(null);
        // Redirect to login if on a protected route
        if (pathname?.startsWith("/workspace")) {
          router.push(buildLoginUrl(pathname));
        }
      }
    } catch (err) {
      console.error("Failed to refresh user:", err);
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, [staticMode, pathname, router]);

  /**
   * Logout - call FastAPI logout endpoint and clear local state
   * Per RFC-001: Immediately clear local state, don't wait for server confirmation
   *
   * When the gateway is unreachable the fetch silently fails — the SPA
   * router.push("/") would leave the user on "/" still holding stale
   * React state and any in-flight SSE / fetch / query subscriptions.
   * We therefore fall back to a hard navigation (window.location.href),
   * which discards all client state the same way the legacy form-POST
   * logout used to.
   */
  const logout = useCallback(async () => {
    // Immediately clear local state and reconnect cursors to prevent UI flicker
    clearStreamReconnectKeys();
    setUser(null);

    if (staticMode) {
      router.push("/");
      return;
    }

    let logoutFailed = false;
    try {
      const res = await fetcher("/api/v1/auth/logout", {
        method: "POST",
      });
      if (!res.ok) logoutFailed = true;
    } catch (err) {
      console.error("Logout request failed:", err);
      logoutFailed = true;
    }

    if (logoutFailed && typeof window !== "undefined") {
      // Hard navigation ensures every in-flight subscription is torn down,
      // matching the legacy form-POST logout behaviour during a gateway outage.
      window.location.href = "/";
      return;
    }

    // Redirect to home page
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
