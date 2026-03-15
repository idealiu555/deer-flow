"use client";

import {
  Tooltip as TooltipPrimitive,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

export function Tooltip({
  children,
  content,
  triggerId,
  ...props
}: {
  children: React.ReactNode;
  content?: React.ReactNode;
  triggerId?: string;
}) {
  return (
    <TooltipPrimitive delayDuration={500} {...props}>
      <TooltipTrigger asChild id={triggerId}>
        {children}
      </TooltipTrigger>
      <TooltipContent>{content}</TooltipContent>
    </TooltipPrimitive>
  );
}
