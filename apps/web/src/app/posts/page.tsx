import { PageHeader } from "@/components/page";
import { PostsView } from "@/components/views/posts-view";

export default function PostsPage() {
  return (
    <>
      <PageHeader
        title="Posts"
        description="What is scheduled or on hold, soonest first, and what was posted. Times are Korea time (KST)."
      />
      <PostsView />
    </>
  );
}
