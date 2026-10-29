import { PageHeader } from "@/components/page";
import { InstagramView } from "@/components/views/instagram-view";

export default function InstagramPage() {
  return (
    <>
      <PageHeader
        title="Instagram accounts"
        description="The Organization's own Instagram accounts that approved decks can be posted to. Admins connect and disconnect them."
      />
      <InstagramView />
    </>
  );
}
