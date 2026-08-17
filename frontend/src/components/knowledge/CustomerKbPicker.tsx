"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Loader2, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
  DialogClose,
} from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { getCorpora, createPatientCorpus } from "@/lib/api/corpora";

interface CustomerKbPickerProps {
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function CustomerKbPicker({ selectedId, onSelect }: CustomerKbPickerProps) {
  const [dialogOpen, setDialogOpen] = useState(false);
  const [label, setLabel] = useState("");
  const queryClient = useQueryClient();

  const corporaQuery = useQuery({ queryKey: ["corpora"], queryFn: getCorpora });
  const patients = corporaQuery.data?.patients ?? [];

  const createMutation = useMutation({
    mutationFn: () => createPatientCorpus(label.trim()),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["corpora"] });
      onSelect(data.id);
      setLabel("");
      setDialogOpen(false);
    },
  });

  return (
    <div className="flex items-center gap-1.5 p-3 pb-0">
      <Users className="size-3.5 shrink-0 text-muted-foreground" />
      <Select value={selectedId ?? undefined} onValueChange={(v) => v && onSelect(v)}>
        <SelectTrigger size="sm" className="min-w-0 flex-1">
          <SelectValue placeholder={patients.length ? "Select a customer KB…" : "No customer KBs yet"} />
        </SelectTrigger>
        <SelectContent>
          {patients.map((p) => (
            <SelectItem key={p.id} value={p.id}>
              {p.label} {p.built ? "✓" : ""}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>

      <Button size="sm" variant="outline" className="shrink-0 gap-1.5" onClick={() => setDialogOpen(true)}>
        <Plus className="size-3.5" /> New
      </Button>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New customer knowledge base</DialogTitle>
            <DialogDescription>
              Creates a new, empty per-customer corpus fused against the Clinical Guidelines KB.
              Upload that customer&apos;s PDFs into it once it&apos;s created.
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="customer-kb-label">Customer name</Label>
            <Input
              id="customer-kb-label"
              value={label}
              onChange={(e) => setLabel(e.target.value)}
              placeholder="e.g. Acme Diagnostics"
              autoFocus
            />
          </div>
          {createMutation.isError && (
            <p className="text-xs text-destructive">
              {createMutation.error instanceof Error ? createMutation.error.message : "Failed to create"}
            </p>
          )}
          <DialogFooter>
            <DialogClose render={<Button variant="outline" size="sm" />}>Cancel</DialogClose>
            <Button
              size="sm"
              onClick={() => createMutation.mutate()}
              disabled={!label.trim() || createMutation.isPending}
              className="gap-1.5"
            >
              {createMutation.isPending && <Loader2 className="size-3.5 animate-spin" />}
              Create
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
