"use client";

import { DocumentProcessing } from "@/components/knowledge/DocumentProcessing";
import { KnowledgeExplorer } from "@/components/knowledge/KnowledgeExplorer";
import { CustomerKbPicker } from "@/components/knowledge/CustomerKbPicker";
import { PatientTopicPanel } from "@/components/knowledge/PatientTopicPanel";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { useChatStore } from "@/lib/chat-store";

export function KnowledgeWorkspace() {
  const selectedPatientId = useChatStore((s) => s.selectedPatientId);
  const setSelectedPatientId = useChatStore((s) => s.setSelectedPatientId);

  return (
    <div className="grid h-full min-h-0 min-w-0 grid-rows-2 divide-y md:grid-cols-2 md:grid-rows-1 md:divide-x md:divide-y-0">
      {/* Left — permanent, shared Clinical Guidelines KB */}
      <div className="flex min-h-0 min-w-0 flex-col divide-y">
        <div className="max-h-[45%] min-w-0 overflow-x-hidden overflow-y-auto">
          <DocumentProcessing mode="guidelines" corpusId="guidelines" />
        </div>
        <div className="min-h-0 min-w-0 flex-1">
          <KnowledgeExplorer corpusId="guidelines" />
        </div>
      </div>

      {/* Right — per-customer patient KB, fused against the guidelines KB */}
      <div className="flex min-h-0 min-w-0 flex-col divide-y">
        <CustomerKbPicker selectedId={selectedPatientId} onSelect={setSelectedPatientId} />
        {selectedPatientId ? (
          <>
            <div className="max-h-[45%] min-w-0 overflow-x-hidden overflow-y-auto">
              <DocumentProcessing mode="patient" corpusId={selectedPatientId} />
            </div>
            <div className="flex min-h-0 min-w-0 flex-1 flex-col">
              <Tabs defaultValue="topics" className="flex min-h-0 flex-1 flex-col gap-0">
                <TabsList className="mx-2 mt-1.5 w-fit">
                  <TabsTrigger value="topics">Topics</TabsTrigger>
                  <TabsTrigger value="files">Files</TabsTrigger>
                </TabsList>
                <TabsContent value="topics" className="min-h-0 flex-1">
                  <PatientTopicPanel corpusId={selectedPatientId} />
                </TabsContent>
                <TabsContent value="files" className="min-h-0 flex-1">
                  <KnowledgeExplorer corpusId={selectedPatientId} />
                </TabsContent>
              </Tabs>
            </div>
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center p-6 text-center text-xs text-muted-foreground">
            Select or create a customer knowledge base to upload and process its documents.
          </div>
        )}
      </div>
    </div>
  );
}
