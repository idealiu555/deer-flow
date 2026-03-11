import { formatDistanceToNow } from "date-fns";
import { zhCN as dateFnsZhCN } from "date-fns/locale";

export function formatTimeAgo(date: Date | string | number) {
  return formatDistanceToNow(date, {
    addSuffix: true,
    locale: dateFnsZhCN,
  });
}
