import { PageHeader } from "@/components/page";
import { ReviewQueueView } from "@/components/views/review-queue-view";

export default function ReviewQueuePage() {
  return (
    <>
      <PageHeader title="Review" />
      <ReviewQueueView />
    </>
  );
}
