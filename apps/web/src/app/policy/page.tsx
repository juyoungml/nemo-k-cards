import { Download } from "lucide-react";

import { PageHeader } from "@/components/page";
import { PolicyLogView } from "@/components/views/policy-view";
import { Button } from "@/components/ui/button";

export default function PolicyLogPage() {
  return (
    <>
      <PageHeader
        title="Policy Log"
        description="OpenShell sandbox network & filesystem events · source: openshell logs <sandbox> --source sandbox"
      >
        <Button variant="outline" size="lg">
          <Download /> Export
        </Button>
      </PageHeader>
      <PolicyLogView />
    </>
  );
}
