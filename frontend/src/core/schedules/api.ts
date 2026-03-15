import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";

import { getBackendBaseURL } from "@/core/config";

export type ScheduleKind = "cron" | "once";

export interface ScheduleItem {
  id: string;
  owner_key: string;
  owner_channel?: string | null;
  owner_user?: string | null;
  channel_name?: string | null;
  chat_id?: string | null;
  topic_id?: string | null;
  thread_id?: string | null;
  assistant_id: string;
  title: string;
  prompt: string;
  kind: ScheduleKind;
  cron?: string | null;
  at?: string | null;
  timezone: string;
  status: "active" | "paused";
  next_run_at?: string | null;
  last_error?: string | null;
  config: Record<string, unknown>;
  context: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface ScheduleRun {
  id: string;
  schedule_id: string;
  planned_at?: string | null;
  started_at: string;
  finished_at?: string | null;
  status: "running" | "success" | "failed";
  attempt: number;
  error?: string | null;
  output?: string | null;
  created_at: string;
}

export interface SchedulerStatus {
  enabled: boolean;
  db_path: string;
  total: number;
  active: number;
  paused: number;
  due: number;
  running: number;
  drafts: number;
  service_running?: boolean;
}

export interface ApiResponse<T extends object = Record<string, unknown>> {
  success: boolean;
  message: string;
  data: T;
}

export const SETTINGS_OWNER_KEY = "web:settings";

export interface ScheduleFormInput {
  title: string;
  prompt: string;
  kind: "cron" | "once";
  timezone?: string;
  cron?: string;
  at?: string;
  threadId?: string;
}

function resolveOwnerKey(ownerKey?: string): string {
  return ownerKey ?? SETTINGS_OWNER_KEY;
}

async function unwrap<T extends object>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed (${res.status})`);
  }
  const body = (await res.json()) as ApiResponse<T>;
  if (!body.success) {
    throw new Error(body.message || "Request failed");
  }
  return body.data;
}

export async function listSchedules(): Promise<ScheduleItem[]> {
  const qs = `?owner_key=${encodeURIComponent(SETTINGS_OWNER_KEY)}`;
  const res = await fetch(`${getBackendBaseURL()}/api/schedules${qs}`);
  const data = await unwrap<{ schedules: ScheduleItem[] }>(res);
  return data.schedules;
}

export async function getSchedulerStatus(): Promise<SchedulerStatus> {
  const res = await fetch(`${getBackendBaseURL()}/api/schedules/status`);
  const data = await unwrap<{ scheduler_status: SchedulerStatus }>(res);
  return data.scheduler_status;
}

export async function createSchedule(input: ScheduleFormInput): Promise<ScheduleItem> {
  const res = await fetch(`${getBackendBaseURL()}/api/schedules`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      schedule: {
        title: input.title,
        prompt: input.prompt,
        kind: input.kind,
        timezone: input.timezone,
        cron: input.cron,
        at: input.at,
      },
      owner_key: SETTINGS_OWNER_KEY,
      thread_id: input.threadId,
      confirmed: true,
    }),
  });
  const data = await unwrap<{ schedule: ScheduleItem }>(res);
  return data.schedule;
}

export async function pauseSchedule(scheduleId: string, ownerKey?: string): Promise<ScheduleItem> {
  const scopedOwner = resolveOwnerKey(ownerKey);
  const res = await fetch(`${getBackendBaseURL()}/api/schedules/${scheduleId}/pause`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ owner_key: scopedOwner }),
  });
  const data = await unwrap<{ schedule: ScheduleItem }>(res);
  return data.schedule;
}

export async function resumeSchedule(scheduleId: string, ownerKey?: string): Promise<ScheduleItem> {
  const scopedOwner = resolveOwnerKey(ownerKey);
  const res = await fetch(`${getBackendBaseURL()}/api/schedules/${scheduleId}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ owner_key: scopedOwner }),
  });
  const data = await unwrap<{ schedule: ScheduleItem }>(res);
  return data.schedule;
}

export async function triggerSchedule(scheduleId: string, ownerKey?: string): Promise<ScheduleItem> {
  const scopedOwner = resolveOwnerKey(ownerKey);
  const res = await fetch(`${getBackendBaseURL()}/api/schedules/${scheduleId}/trigger`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ owner_key: scopedOwner, confirmed: true }),
  });
  const data = await unwrap<{ schedule: ScheduleItem }>(res);
  return data.schedule;
}

export async function deleteSchedule(scheduleId: string, ownerKey?: string): Promise<void> {
  const scopedOwner = resolveOwnerKey(ownerKey);
  const qs = `?owner_key=${encodeURIComponent(scopedOwner)}&confirmed=true`;
  const res = await fetch(`${getBackendBaseURL()}/api/schedules/${scheduleId}${qs}`, {
    method: "DELETE",
  });
  await unwrap(res);
}

export async function listScheduleRuns(scheduleId: string, ownerKey?: string): Promise<ScheduleRun[]> {
  const scopedOwner = resolveOwnerKey(ownerKey);
  const qs = `?owner_key=${encodeURIComponent(scopedOwner)}`;
  const res = await fetch(`${getBackendBaseURL()}/api/schedules/${scheduleId}/runs${qs}`);
  const data = await unwrap<{ runs: ScheduleRun[] }>(res);
  return data.runs;
}

const SCHEDULES_QUERY_KEY = ["schedules"] as const;
const SCHEDULER_STATUS_QUERY_KEY = ["scheduler-status"] as const;

function invalidateScheduleQueries(queryClient: QueryClient) {
  void queryClient.invalidateQueries({ queryKey: SCHEDULES_QUERY_KEY });
  void queryClient.invalidateQueries({ queryKey: SCHEDULER_STATUS_QUERY_KEY });
}

function useScheduleMutation<TVars, TData>(mutationFn: (vars: TVars) => Promise<TData>) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn,
    onSuccess: () => {
      invalidateScheduleQueries(queryClient);
    },
  });
}

export function useSchedules() {
  const { data, isLoading, error } = useQuery({
    queryKey: SCHEDULES_QUERY_KEY,
    queryFn: () => listSchedules(),
  });
  return { schedules: data ?? [], isLoading, error };
}

export function useSchedulerStatus() {
  const { data, isLoading, error } = useQuery({
    queryKey: SCHEDULER_STATUS_QUERY_KEY,
    queryFn: () => getSchedulerStatus(),
    refetchInterval: 30000,
  });
  return { status: data, isLoading, error };
}

export function useCreateSchedule() {
  return useScheduleMutation((input: ScheduleFormInput) => createSchedule(input));
}

export function usePauseSchedule() {
  return useScheduleMutation(({ scheduleId, ownerKey }: { scheduleId: string; ownerKey?: string }) =>
    pauseSchedule(scheduleId, ownerKey)
  );
}

export function useResumeSchedule() {
  return useScheduleMutation(({ scheduleId, ownerKey }: { scheduleId: string; ownerKey?: string }) =>
    resumeSchedule(scheduleId, ownerKey)
  );
}

export function useTriggerSchedule() {
  return useScheduleMutation(({ scheduleId, ownerKey }: { scheduleId: string; ownerKey?: string }) =>
    triggerSchedule(scheduleId, ownerKey)
  );
}

export function useDeleteSchedule() {
  return useScheduleMutation(({ scheduleId, ownerKey }: { scheduleId: string; ownerKey?: string }) =>
    deleteSchedule(scheduleId, ownerKey)
  );
}

export function useScheduleRuns(scheduleId: string | null, ownerKey?: string) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["schedule-runs", scheduleId, ownerKey],
    queryFn: () => listScheduleRuns(scheduleId!, ownerKey),
    enabled: Boolean(scheduleId),
  });
  return { runs: data ?? [], isLoading, error };
}
