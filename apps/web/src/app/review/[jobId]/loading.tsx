// Suspense boundary for the dynamic `params` access (required with cacheComponents).
export default function Loading() {
  return (
    <div className="flex animate-pulse items-start gap-5">
      <div className="aspect-[4/5] w-[400px] rounded-xl bg-muted" />
      <div className="flex-1 space-y-4">
        <div className="h-28 rounded-xl bg-muted" />
        <div className="h-64 rounded-xl bg-muted" />
      </div>
    </div>
  );
}
