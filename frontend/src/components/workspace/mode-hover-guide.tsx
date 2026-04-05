"use client";

import { useI18n } from "@/core/i18n/hooks";
import type { Translations } from "@/core/i18n/locales/types";

import { Tooltip } from "./tooltip";

export type AgentMode = "common" | "pro";

function getModeLabelKey(
  mode: AgentMode,
): keyof Pick<
  Translations["inputBox"],
  "commonMode" | "proMode"
> {
  switch (mode) {
    case "common":
      return "commonMode";
    case "pro":
      return "proMode";
  }
}

function getModeDescriptionKey(
  mode: AgentMode,
): keyof Pick<
  Translations["inputBox"],
  "commonModeDescription" | "proModeDescription"
> {
  switch (mode) {
    case "common":
      return "commonModeDescription";
    case "pro":
      return "proModeDescription";
  }
}

export function ModeHoverGuide({
  mode,
  children,
  showTitle = true,
  triggerId,
}: {
  mode: AgentMode;
  children: React.ReactNode;
  /** When true, tooltip shows "ModeName: Description". When false, only description. */
  showTitle?: boolean;
  triggerId?: string;
}) {
  const { t } = useI18n();
  const label = t.inputBox[getModeLabelKey(mode)];
  const description = t.inputBox[getModeDescriptionKey(mode)];
  const content = showTitle ? `${label}: ${description}` : description;

  return (
    <Tooltip content={content} triggerId={triggerId}>
      {children}
    </Tooltip>
  );
}
