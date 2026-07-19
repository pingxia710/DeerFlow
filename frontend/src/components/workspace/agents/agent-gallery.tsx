"use client";

import { BotIcon, PlusIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { beginThreadNavigation } from "@/components/workspace/chats";
import { useAgents, useRoles } from "@/core/agents";
import { useI18n } from "@/core/i18n/hooks";
import { useModels } from "@/core/models/hooks";

import { AgentCard } from "./agent-card";
import { RoleCard } from "./role-card";

export function AgentGallery() {
  const { t } = useI18n();
  const { agents, isLoading: agentsLoading } = useAgents();
  const { roles, isLoading: rolesLoading } = useRoles();
  const { models } = useModels();
  const router = useRouter();
  const [activeTab, setActiveTab] = useState("agents");

  const handleNewAgent = () => {
    const nextPath = "/workspace/agents/new";
    beginThreadNavigation(nextPath);
    router.push(nextPath);
  };

  return (
    <div className="flex size-full flex-col">
      {/* Page header */}
      <div className="flex items-center justify-between border-b px-6 py-4">
        <div>
          <h1 className="text-xl font-semibold">{t.agents.title}</h1>
          <p className="text-muted-foreground mt-0.5 text-sm">
            {t.agents.description}
          </p>
        </div>
        {activeTab === "agents" ? (
          <Button onClick={handleNewAgent}>
            <PlusIcon className="mr-1.5 h-4 w-4" />
            {t.agents.newAgent}
          </Button>
        ) : null}
      </div>

      {/* Content */}
      <Tabs
        value={activeTab}
        onValueChange={setActiveTab}
        className="min-h-0 flex-1 gap-0"
      >
        <div className="border-b px-6 pt-3">
          <TabsList variant="line">
            <TabsTrigger value="agents">{t.agents.agentsTab}</TabsTrigger>
            <TabsTrigger value="roles">{t.agents.rolesTab}</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="agents" className="overflow-y-auto p-6">
          {agentsLoading ? (
            <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
              {t.common.loading}
            </div>
          ) : agents.length === 0 ? (
            <div className="flex h-64 flex-col items-center justify-center gap-3 text-center">
              <div className="bg-muted flex h-14 w-14 items-center justify-center rounded-full">
                <BotIcon className="text-muted-foreground h-7 w-7" />
              </div>
              <div>
                <p className="font-medium">{t.agents.emptyTitle}</p>
                <p className="text-muted-foreground mt-1 text-sm">
                  {t.agents.emptyDescription}
                </p>
              </div>
              <Button
                variant="outline"
                className="mt-2"
                onClick={handleNewAgent}
              >
                <PlusIcon className="mr-1.5 h-4 w-4" />
                {t.agents.newAgent}
              </Button>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {agents.map((agent) => (
                <AgentCard key={agent.name} agent={agent} models={models} />
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="roles" className="overflow-y-auto p-6">
          {rolesLoading ? (
            <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
              {t.common.loading}
            </div>
          ) : roles.length === 0 ? (
            <div className="text-muted-foreground flex h-40 items-center justify-center text-sm">
              {t.agents.emptyRoles}
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
              {roles.map((role) => (
                <RoleCard key={role.name} role={role} models={models} />
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
