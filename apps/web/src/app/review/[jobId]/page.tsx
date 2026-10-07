import { ReviewDetailView } from "@/components/views/review-detail-view";

export default async function ReviewDetailPage(props: PageProps<"/review/[jobId]">) {
  const { jobId } = await props.params;
  return <ReviewDetailView id={jobId} />;
}
