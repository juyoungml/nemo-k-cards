import Link from "next/link";
import { Plus } from "lucide-react";

import { PageHeader } from "@/components/page";
import { RoleOnly } from "@/components/role-gate";
import { DashboardView } from "@/components/views/dashboard-view";
import { Button } from "@/components/ui/button";

export default function DashboardPage() {
  return (
    <>
      <PageHeader title="Dashboard" description="Instagram accounts, reach and recent decks for @whatsonkorea">
        <RoleOnly min="editor">
          <Button size="lg" asChild>
            <Link href="/jobs/new">
              <Plus /> New deck
            </Link>
          </Button>
        </RoleOnly>
      </PageHeader>
      <DashboardView />
    </>
  );
}
