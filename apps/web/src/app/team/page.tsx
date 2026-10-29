import { PageHeader } from "@/components/page";
import { TeamView } from "@/components/views/team-view";

export default function TeamPage() {
  return (
    <>
      <PageHeader
        title="Team"
        description="Viewers see everything. Editors also start decks and Brainstorms. Approvers also approve and reject. Admins also manage the team."
      />
      <TeamView />
    </>
  );
}
