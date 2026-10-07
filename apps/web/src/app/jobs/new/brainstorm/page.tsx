import { ModeSwitch } from "@/components/mode-switch";
import { PageHeader } from "@/components/page";
import { StatusBadge } from "@/components/status-badge";
import { BrainstormView } from "@/components/views/brainstorm-view";

export default function BrainstormPage() {
  return (
    <>
      <PageHeader
        title="New Job"
        description="Plan the card news with the agent, then hand it to the same verify → render → review pipeline."
      >
        <StatusBadge tone="info">Draft · auto-saved</StatusBadge>
      </PageHeader>
      <ModeSwitch active="brainstorm" />
      <BrainstormView />
    </>
  );
}
