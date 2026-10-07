// Suspense boundary for the dynamic `params` access (required with cacheComponents).
export default function Loading() {
  return (
    <div className="flex animate-pulse flex-col gap-5 lg:flex-row lg:items-start">
      <div className="mx-auto aspect-[4/5] w-full max-w-[400px] rounded-xl bg-muted lg:mx-0" />
      <div className="flex-1 space-y-4">
        <div className="h-28 rounded-xl bg-muted" />
        <div className="h-64 rounded-xl bg-muted" />
      </div>
    </div>
  );
}
