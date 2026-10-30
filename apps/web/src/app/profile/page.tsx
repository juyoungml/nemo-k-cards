import { PageHeader } from "@/components/page";
import { ProfileView } from "@/components/views/profile-view";

export default function ProfilePage() {
  return (
    <>
      <PageHeader
        title="Organization profile"
        description="What the agents know about your Organization. Every new deck and Brainstorm uses it. Admins edit it; everyone can read it."
      />
      <ProfileView />
    </>
  );
}
