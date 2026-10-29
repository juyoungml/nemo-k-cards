import { PageHeader } from "@/components/page";
import { AuditView } from "@/components/views/audit-view";

export default function AuditPage() {
  return (
    <>
      <PageHeader
        title="Audit log"
        description="Approvals, Overrides, rejections, edits and Member changes in this Organization. Admins only."
      />
      <AuditView />
    </>
  );
}
