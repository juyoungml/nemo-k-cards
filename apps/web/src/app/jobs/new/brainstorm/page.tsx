import { Brainstorm } from "@/components/brainstorm";
import { ModeSwitch } from "@/components/mode-switch";
import { PageHeader } from "@/components/page";
import { StatusBadge } from "@/components/status-badge";
import { getDraft } from "@/lib/api";
import { targetOptions, toneOptions } from "@/lib/mock";

export default async function BrainstormPage() {
  const draft = await getDraft();

  return (
    <>
      <PageHeader
        title="New Job"
        description="Plan the card news with the agent, then hand it to the same verify → render → review pipeline."
      >
        <StatusBadge tone="info">Draft · auto-saved</StatusBadge>
      </PageHeader>
      <ModeSwitch active="brainstorm" />
      <Brainstorm initialDraft={draft} targetOptions={targetOptions} toneOptions={toneOptions} />
    </>
  );
}
