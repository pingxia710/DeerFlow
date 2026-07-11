"use client";

import {
  QueryClient,
  QueryClientProvider as TanStackQueryClientProvider,
} from "@tanstack/react-query";
import { useEffect, useMemo } from "react";

import { useAuth } from "@/core/auth/AuthProvider";

function createIdentityScopedQueryClient(_identity: string) {
  return new QueryClient();
}

export function QueryClientProvider({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const { user } = useAuth();
  const cacheIdentity = user?.id ?? "anonymous";
  const queryClient = useMemo(
    () => createIdentityScopedQueryClient(cacheIdentity),
    [cacheIdentity],
  );

  useEffect(
    () => () => {
      void queryClient.cancelQueries();
      queryClient.clear();
    },
    [queryClient],
  );

  return (
    <TanStackQueryClientProvider key={cacheIdentity} client={queryClient}>
      {children}
    </TanStackQueryClientProvider>
  );
}
