import Link from "next/link";
import { Plus } from "lucide-react";

import { PageHeader } from "@/components/page";
import { DashboardView } from "@/components/views/dashboard-view";
import { Button } from "@/components/ui/button";

export default function DashboardPage() {
  return (
    <>
      <PageHeader title="Dashboard" description="Channels, reach and recent card news for @whatsonkorea">
        <Button size="lg" asChild>
          <Link href="/jobs/new">
            <Plus /> New Job
          </Link>
        </Button>
      </PageHeader>
      <DashboardView />
    </>
  );
}
