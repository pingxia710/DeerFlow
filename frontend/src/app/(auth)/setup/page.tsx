"use client";

import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { FlickeringGrid } from "@/components/ui/flickering-grid";
import { Input } from "@/components/ui/input";
import { fetch as fetcher } from "@/core/api/fetcher";
import { useAuth } from "@/core/auth/AuthProvider";
import { parseAuthError } from "@/core/auth/types";
import { getBackendBaseURL } from "@/core/config";

type SetupMode = "loading" | "init_admin" | "change_password";

export default function SetupPage() {
  const router = useRouter();
  const { user, isAuthenticated } = useAuth();
  const { theme, resolvedTheme } = useTheme();
  const [mode, setMode] = useState<SetupMode>("loading");

  // --- Shared state ---
  const [email, setEmail] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // --- Change-password mode only ---
  const [currentPassword, setCurrentPassword] = useState("");

  useEffect(() => {
    let cancelled = false;

    if (isAuthenticated && user?.needs_setup) {
      setMode("change_password");
    } else if (!isAuthenticated) {
      // Check if the system has no users yet
      void fetcher(`${getBackendBaseURL()}/api/v1/auth/setup-status`)
        .then((r) => r.json())
        .then((data: { needs_setup?: boolean }) => {
          if (cancelled) return;
          if (data.needs_setup) {
            setMode("init_admin");
          } else {
            // System already set up and user is not logged in — go to login
            router.push("/login");
          }
        })
        .catch(() => {
          if (!cancelled) router.push("/login");
        });
    } else {
      // Authenticated but needs_setup is false — already set up
      router.push("/workspace");
    }

    return () => {
      cancelled = true;
    };
  }, [isAuthenticated, user, router]);

  // ── Init-admin handler ─────────────────────────────────────────────
  const handleInitAdmin = async (e: React.SubmitEvent) => {
    e.preventDefault();
    setError("");

    if (newPassword !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }

    setLoading(true);
    try {
      const res = await fetcher(
        `${getBackendBaseURL()}/api/v1/auth/initialize`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            email,
            password: newPassword,
          }),
        },
      );

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        setError(authError.message);
        return;
      }

      router.push("/workspace");
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  // ── Change-password handler ────────────────────────────────────────
  const handleChangePassword = async (e: React.SubmitEvent) => {
    e.preventDefault();
    setError("");

    if (newPassword !== confirmPassword) {
      setError("Passwords do not match");
      return;
    }
    if (newPassword.length < 8) {
      setError("Password must be at least 8 characters");
      return;
    }

    setLoading(true);
    try {
      const res = await fetcher(
        `${getBackendBaseURL()}/api/v1/auth/change-password`,
        {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
          },
          body: JSON.stringify({
            current_password: currentPassword,
            new_password: newPassword,
            new_email: email || undefined,
          }),
        },
      );

      if (!res.ok) {
        const data = await res.json();
        const authError = parseAuthError(data);
        setError(authError.message);
        return;
      }

      router.push("/workspace");
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const actualTheme = theme === "system" ? resolvedTheme : theme;

  if (mode === "loading") {
    return (
      <div className="flex min-h-screen items-center justify-center">
        <p className="text-muted-foreground text-sm">Loading…</p>
      </div>
    );
  }

  // ── Admin initialization form ──────────────────────────────────────
  if (mode === "init_admin") {
    return (
      <div className="bg-background flex min-h-screen items-center justify-center">
        <FlickeringGrid
          className="absolute inset-0 z-0 mask-[url(/images/deer.svg)] mask-size-[100vw] mask-center mask-no-repeat md:mask-size-[72vh]"
          squareSize={4}
          gridGap={4}
          color={actualTheme === "dark" ? "white" : "black"}
          maxOpacity={0.3}
          flickerChance={0.25}
        />
        <div className="border-border/20 bg-background/5 w-full max-w-md space-y-6 rounded-3xl border p-8 backdrop-blur-sm">
          <div className="text-center">
            <h1 className="font-serif text-3xl">DeerFlow</h1>
            <p className="text-muted-foreground mt-2">Create admin account</p>
            <p className="text-muted-foreground mt-1 text-xs">
              Set up the administrator account to get started.
            </p>
          </div>
          <form
            aria-busy={loading}
            className="space-y-2"
            onSubmit={handleInitAdmin}
          >
            <div className="flex flex-col space-y-1">
              <label htmlFor="email" className="text-sm font-medium">
                Email
              </label>
              <Input
                id="email"
                type="email"
                placeholder="you@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                aria-describedby={error ? "init-admin-error" : undefined}
                aria-invalid={Boolean(error)}
                className="min-h-11"
              />
            </div>
            <div className="flex flex-col space-y-1">
              <label htmlFor="password" className="text-sm font-medium">
                Password
              </label>
              <Input
                id="password"
                type="password"
                placeholder="Password (min. 8 characters)"
                value={newPassword}
                onChange={(e) => setNewPassword(e.target.value)}
                required
                minLength={8}
                aria-describedby={error ? "init-admin-error" : undefined}
                aria-invalid={Boolean(error)}
                className="min-h-11"
              />
            </div>
            <div className="flex flex-col space-y-1">
              <label htmlFor="confirmPassword" className="text-sm font-medium">
                Confirm Password
              </label>
              <Input
                id="confirmPassword"
                type="password"
                placeholder="Confirm password"
                value={confirmPassword}
                onChange={(e) => setConfirmPassword(e.target.value)}
                required
                minLength={8}
                aria-describedby={error ? "init-admin-error" : undefined}
                aria-invalid={Boolean(error)}
                className="min-h-11"
              />
            </div>
            {error && (
              <p
                className="ms-1 text-sm text-red-700 dark:text-red-300"
                id="init-admin-error"
                role="alert"
              >
                {error}
              </p>
            )}
            <p aria-live="polite" className="sr-only">
              {loading ? "Creating account…" : ""}
            </p>
            <Button
              type="submit"
              className="min-h-11 w-full"
              disabled={loading}
            >
              {loading ? "Creating account…" : "Create Admin Account"}
            </Button>
          </form>
        </div>
      </div>
    );
  }

  // ── Change-password form (needs_setup after login) ─────────────────
  return (
    <div className="bg-background flex min-h-screen items-center justify-center">
      <FlickeringGrid
        className="absolute inset-0 z-0 mask-[url(/images/deer.svg)] mask-size-[100vw] mask-center mask-no-repeat md:mask-size-[72vh]"
        squareSize={4}
        gridGap={4}
        color={actualTheme === "dark" ? "white" : "black"}
        maxOpacity={0.3}
        flickerChance={0.25}
      />
      <div className="border-border/20 bg-background/5 w-full max-w-md space-y-6 rounded-3xl border p-8 backdrop-blur-sm">
        <div className="text-center">
          <h1 className="font-serif text-3xl">DeerFlow</h1>
          <p className="text-muted-foreground mt-2">
            Complete admin account setup
          </p>
          <p className="text-muted-foreground mt-1 text-xs">
            Set your real email and a new password.
          </p>
        </div>
        <form
          aria-busy={loading}
          className="space-y-4"
          onSubmit={handleChangePassword}
        >
          <div className="flex flex-col space-y-1">
            <label htmlFor="change-email" className="text-sm font-medium">
              Email
            </label>
            <Input
              id="change-email"
              type="email"
              placeholder="Your email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              required
              aria-describedby={error ? "change-password-error" : undefined}
              aria-invalid={Boolean(error)}
              className="min-h-11"
            />
          </div>
          <div className="flex flex-col space-y-1">
            <label htmlFor="current-password" className="text-sm font-medium">
              Current password
            </label>
            <Input
              id="current-password"
              type="password"
              placeholder="Current password"
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              required
              aria-describedby={error ? "change-password-error" : undefined}
              aria-invalid={Boolean(error)}
              className="min-h-11"
            />
          </div>
          <div className="flex flex-col space-y-1">
            <label htmlFor="new-password" className="text-sm font-medium">
              New password
            </label>
            <Input
              id="new-password"
              type="password"
              placeholder="New password"
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
              required
              minLength={8}
              aria-describedby={error ? "change-password-error" : undefined}
              aria-invalid={Boolean(error)}
              className="min-h-11"
            />
          </div>
          <div className="flex flex-col space-y-1">
            <label
              htmlFor="confirm-new-password"
              className="text-sm font-medium"
            >
              Confirm new password
            </label>
            <Input
              id="confirm-new-password"
              type="password"
              placeholder="Confirm new password"
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              required
              minLength={8}
              aria-describedby={error ? "change-password-error" : undefined}
              aria-invalid={Boolean(error)}
              className="min-h-11"
            />
          </div>
          {error && (
            <p
              className="text-sm text-red-700 dark:text-red-300"
              id="change-password-error"
              role="alert"
            >
              {error}
            </p>
          )}
          <p aria-live="polite" className="sr-only">
            {loading ? "Setting up…" : ""}
          </p>
          <Button type="submit" className="min-h-11 w-full" disabled={loading}>
            {loading ? "Setting up…" : "Complete Setup"}
          </Button>
        </form>
      </div>
    </div>
  );
}
