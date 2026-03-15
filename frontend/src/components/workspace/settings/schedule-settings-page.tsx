"use client";

import { ChevronDownIcon, ClockIcon, LoaderIcon, PlayIcon, TrashIcon } from "lucide-react";
import { useParams } from "next/navigation";
import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import {
  Item,
  ItemActions,
  ItemContent,
  ItemDescription,
  ItemTitle,
} from "@/components/ui/item";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/core/i18n/hooks";
import {
  type ScheduleItem,
  useCreateSchedule,
  useDeleteSchedule,
  usePauseSchedule,
  useResumeSchedule,
  useScheduleRuns,
  useSchedules,
  useSchedulerStatus,
  useTriggerSchedule,
} from "@/core/schedules/api";

import { SettingsSection } from "./settings-section";

interface ScheduleCardProps {
  item: ScheduleItem;
  onToggleSchedule: (scheduleId: string, ownerKey: string, enabled: boolean) => void;
  onTriggerSchedule: (scheduleId: string, ownerKey: string) => void;
  onDeleteSchedule: (scheduleId: string, ownerKey: string) => void;
}

function ScheduleCard({
  item,
  onToggleSchedule,
  onTriggerSchedule,
  onDeleteSchedule,
}: ScheduleCardProps) {
  const { t } = useI18n();
  const [runsOpen, setRunsOpen] = useState(false);
  const { runs, isLoading, error } = useScheduleRuns(runsOpen ? item.id : null, item.owner_key);

  return (
    <Item variant="outline" className="items-start">
      <ItemContent>
        <ItemTitle>{item.title}</ItemTitle>
        <ItemDescription className="line-clamp-3">{item.prompt}</ItemDescription>
        <div className="text-muted-foreground text-xs">
          {item.kind === "cron" ? `${item.timezone} | ${item.cron}` : `${item.timezone} | ${item.at}`}
          {item.next_run_at ? ` | next: ${item.next_run_at}` : ""}
        </div>
        {item.last_error ? <div className="text-xs text-red-500">{item.last_error}</div> : null}

        <Collapsible open={runsOpen} onOpenChange={setRunsOpen} className="mt-2 w-full">
          <CollapsibleTrigger asChild>
            <Button size="sm" variant="outline" className="w-full justify-between">
              <span>{runsOpen ? t.settings.schedules.hideRuns : t.settings.schedules.viewRuns}</span>
              <ChevronDownIcon className={`size-4 transition-transform ${runsOpen ? "rotate-180" : ""}`} />
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="mt-2 rounded-md border p-3">
            <div className="mb-2 text-sm font-medium">{t.settings.schedules.recentRuns}</div>
            {isLoading ? (
              <div className="text-muted-foreground text-sm">{t.common.loading}</div>
            ) : error ? (
              <div className="text-sm text-red-500">{String(error.message || error)}</div>
            ) : runs.length === 0 ? (
              <div className="text-muted-foreground text-sm">{t.settings.schedules.noRuns}</div>
            ) : (
              <div className="space-y-2">
                {runs.slice(0, 10).map((run) => (
                  <div key={run.id} className="text-xs">
                    <span className="font-medium">{run.status}</span>
                    {` | ${run.started_at}`}
                    {run.error ? ` | ${run.error}` : ""}
                  </div>
                ))}
              </div>
            )}
          </CollapsibleContent>
        </Collapsible>
      </ItemContent>
      <ItemActions className="flex-wrap justify-end">
        <Switch
          checked={item.status === "active"}
          onCheckedChange={(checked) => onToggleSchedule(item.id, item.owner_key, checked)}
        />
        <Button size="sm" variant="outline" onClick={() => onTriggerSchedule(item.id, item.owner_key)}>
          <PlayIcon className="mr-1 size-3" />
          {t.settings.schedules.runNow}
        </Button>
        <Button size="sm" variant="destructive" onClick={() => onDeleteSchedule(item.id, item.owner_key)}>
          <TrashIcon className="mr-1 size-3" />
          {t.settings.schedules.delete}
        </Button>
      </ItemActions>
    </Item>
  );
}

export function ScheduleSettingsPage() {
  const { t } = useI18n();
  const { schedules, isLoading, error } = useSchedules();
  const { status } = useSchedulerStatus();
  const createScheduleMutation = useCreateSchedule();
  const pauseScheduleMutation = usePauseSchedule();
  const resumeScheduleMutation = useResumeSchedule();
  const triggerScheduleMutation = useTriggerSchedule();
  const deleteScheduleMutation = useDeleteSchedule();

  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [kind, setKind] = useState<"cron" | "once">("cron");
  const [cron, setCron] = useState("0 9 * * *");
  const [at, setAt] = useState("");
  const [timezone, setTimezone] = useState("Asia/Shanghai");
  const { thread_id: threadIdFromPath } = useParams<{
    thread_id?: string;
  }>();
  const threadId =
    threadIdFromPath && threadIdFromPath !== "new"
      ? threadIdFromPath
      : undefined;

  const disabled = createScheduleMutation.isPending;

  const schedulerHint = useMemo(() => {
    if (!status) {
      return "";
    }
    return `${t.settings.schedules.statusLabel}: ${status.active}/${status.total} active`;
  }, [status, t.settings.schedules.statusLabel]);

  const onCreate = async () => {
    if (!title.trim() || !prompt.trim()) {
      return;
    }

    await createScheduleMutation.mutateAsync({
      title: title.trim(),
      prompt: prompt.trim(),
      kind,
      timezone: timezone.trim() || "Asia/Shanghai",
      cron: kind === "cron" ? cron.trim() : undefined,
      at: kind === "once" ? at.trim() : undefined,
      threadId,
    });

    setTitle("");
    setPrompt("");
    if (kind === "once") {
      setAt("");
    }
  };

  const onToggleSchedule = (scheduleId: string, ownerKey: string, enabled: boolean) => {
    if (enabled) {
      resumeScheduleMutation.mutate({ scheduleId, ownerKey });
    } else {
      pauseScheduleMutation.mutate({ scheduleId, ownerKey });
    }
  };

  return (
    <SettingsSection
      title={t.settings.schedules.title}
      description={
        <div className="space-y-1">
          <div>{t.settings.schedules.description}</div>
          {schedulerHint ? (
            <div className="text-muted-foreground text-xs">{schedulerHint}</div>
          ) : null}
        </div>
      }
    >
      <div className="flex flex-col gap-4">
        <Item variant="outline" className="items-start">
          <ItemContent className="gap-3">
            <ItemTitle>{t.settings.schedules.createTitle}</ItemTitle>
            <div className="grid gap-2">
              <Input
                placeholder={t.settings.schedules.namePlaceholder}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
              />
              <Textarea
                rows={3}
                placeholder={t.settings.schedules.promptPlaceholder}
                value={prompt}
                onChange={(e) => setPrompt(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-1 gap-2 md:grid-cols-3">
              <Select value={kind} onValueChange={(value) => setKind(value as "cron" | "once")}>
                <SelectTrigger>
                  <SelectValue placeholder="Select kind" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="cron">cron</SelectItem>
                  <SelectItem value="once">once</SelectItem>
                </SelectContent>
              </Select>
              <Input
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                placeholder="Asia/Shanghai"
              />
              {kind === "cron" ? (
                <Input
                  value={cron}
                  onChange={(e) => setCron(e.target.value)}
                  placeholder="0 9 * * *"
                />
              ) : (
                <Input
                  value={at}
                  onChange={(e) => setAt(e.target.value)}
                  placeholder="2026-03-14T09:00:00+08:00"
                />
              )}
            </div>
          </ItemContent>
          <ItemActions>
            <Button onClick={onCreate} disabled={disabled}>
              {createScheduleMutation.isPending ? (
                <LoaderIcon className="mr-2 size-4 animate-spin" />
              ) : (
                <ClockIcon className="mr-2 size-4" />
              )}
              {t.settings.schedules.createButton}
            </Button>
          </ItemActions>
        </Item>

        {isLoading ? (
          <div className="text-muted-foreground text-sm">{t.common.loading}</div>
        ) : error ? (
          <div className="text-sm text-red-500">{String(error.message || error)}</div>
        ) : schedules.length === 0 ? (
          <div className="text-muted-foreground text-sm">{t.settings.schedules.empty}</div>
        ) : (
          <div className="flex flex-col gap-3">
            {schedules.map((item) => (
              <ScheduleCard
                key={item.id}
                item={item}
                onToggleSchedule={onToggleSchedule}
                onTriggerSchedule={(scheduleId, ownerKey) =>
                  triggerScheduleMutation.mutate({
                    scheduleId,
                    ownerKey,
                  })
                }
                onDeleteSchedule={(scheduleId, ownerKey) =>
                  deleteScheduleMutation.mutate({
                    scheduleId,
                    ownerKey,
                  })
                }
              />
            ))}
          </div>
        )}
      </div>
    </SettingsSection>
  );
}
