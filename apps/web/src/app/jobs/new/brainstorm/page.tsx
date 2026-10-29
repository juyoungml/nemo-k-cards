import { ModeSwitch } from "@/components/mode-switch";
import { PageHeader } from "@/components/page";
import { RoleGate } from "@/components/role-gate";
import { StatusBadge } from "@/components/status-badge";
import { BrainstormView } from "@/components/views/brainstorm-view";

export default function BrainstormPage() {
  return (
    <>
      <PageHeader
        title="New deck"
        description="Plan the card news with the agent, then hand it to the same verify → render → review pipeline."
      >
        <StatusBadge tone="info">Brainstorm · auto-saved</StatusBadge>
      </PageHeader>
      <ModeSwitch active="brainstorm" />
      <RoleGate min="editor" what="Starting a deck">
        <BrainstormView />
      </RoleGate>
    </>
  );
}
