import { ModeSwitch } from "@/components/mode-switch";
import { PageHeader } from "@/components/page";
import { NewJobView } from "@/components/views/new-job-view";

export default function NewJobPage() {
  return (
    <>
      <PageHeader
        title="New Job"
        description="Describe what to research. The agent runs in an OpenShell sandbox; you approve before anything is posted."
      />
      <ModeSwitch active="quick" />
      <NewJobView />
    </>
  );
}
