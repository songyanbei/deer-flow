import { notFound } from "next/navigation";

import { WorkflowVisualDebug } from "@/components/workspace/workflow-visual-debug";

export default async function WorkflowVisualPage({
  params,
}: {
  params: Promise<{ thread_id: string }>;
}) {
  if (process.env.NODE_ENV === "production") {
    notFound();
  }

  const { thread_id } = await params;

  return <WorkflowVisualDebug threadId={thread_id} />;
}
