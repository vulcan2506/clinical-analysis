"use client";

import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { useUIStore } from "@/lib/store";
import { BackendStatus } from "@/components/settings/BackendStatus";
import { ThemeSettings } from "@/components/settings/ThemeSettings";
import { BubbleColorSettings } from "@/components/settings/BubbleColorSettings";

export function SettingsDialog() {
  const { settingsOpen, setSettingsOpen } = useUIStore();

  return (
    <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
        </DialogHeader>
        <BackendStatus />
        <Separator />
        <ThemeSettings />
        <Separator />
        <BubbleColorSettings />
      </DialogContent>
    </Dialog>
  );
}
