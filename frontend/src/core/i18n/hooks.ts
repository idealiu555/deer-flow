import { DEFAULT_LOCALE } from "./locale";
import { zhCN } from "./locales/zh-CN";

export function useI18n() {
  return {
    locale: DEFAULT_LOCALE,
    t: zhCN,
  };
}
