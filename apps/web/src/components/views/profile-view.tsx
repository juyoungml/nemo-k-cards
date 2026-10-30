"use client";

import { LoadState, Panel } from "@/components/page";
import { OrgProfileForm } from "@/components/org-profile-form";
import { api } from "@/lib/api";
import { formatDateTime } from "@/lib/format";
import { atLeast, useMe } from "@/lib/roles";
import { useApi } from "@/lib/use-api";

export function ProfileView() {
  const { role, loading } = useMe();
  const state = useApi(api.getOrgProfile);
  if (loading || !state.data) return <LoadState error={state.error} />;
  const isAdmin = atLeast(role, "admin");
  const { profile, complete, updated_at } = state.data;

  return (
    <Panel
      title={complete ? "Profile" : "Not filled in yet"}
      description={
        complete
          ? `Last saved ${updated_at ? formatDateTime(updated_at) : ""}. Decks started before a change keep the profile they started with.`
          : isAdmin
            ? "Until you fill it in, the agents write as a neutral guide for foreign visitors in Korea."
            : "An Admin hasn't filled it in yet. Until then the agents write as a neutral guide for foreign visitors in Korea."
      }
    >
      <OrgProfileForm
        initial={profile}
        readOnly={!isAdmin}
        onSaved={(s) => state.setData(s)}
      />
      {!isAdmin && <p className="mt-4 text-xs text-muted-foreground">Only Admins can change the profile.</p>}
    </Panel>
  );
}
