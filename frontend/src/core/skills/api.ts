import { getBackendBaseURL } from "@/core/config";

import type { Skill } from "./type";

export async function loadSkills(): Promise<Skill[]> {
  try {
    const response = await fetch(`${getBackendBaseURL()}/api/skills`);
    if (!response.ok) {
      console.error(`Failed to load skills: HTTP ${response.status}`);
      return [];
    }
    const json = await response.json();
    return json.skills as Skill[];
  } catch (error) {
    console.error("Failed to load skills:", error);
    return [];
  }
}

export async function enableSkill(
  skillName: string,
  enabled: boolean,
): Promise<{ enabled: boolean }> {
  const response = await fetch(
    `${getBackendBaseURL()}/api/skills/${skillName}`,
    {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ enabled }),
    },
  );
  if (!response.ok) {
    throw new Error(`Failed to enable skill: HTTP ${response.status}`);
  }
  return response.json();
}

export interface InstallSkillRequest {
  thread_id: string;
  path: string;
}

export interface InstallSkillResponse {
  success: boolean;
  skill_name: string;
  message: string;
}

export async function installSkill(
  request: InstallSkillRequest,
): Promise<InstallSkillResponse> {
  const response = await fetch(`${getBackendBaseURL()}/api/skills/install`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request),
  });

  if (!response.ok) {
    // Handle HTTP error responses (4xx, 5xx)
    const errorData = await response.json().catch(() => ({}));
    const errorMessage =
      errorData.detail ?? `HTTP ${response.status}: ${response.statusText}`;
    return {
      success: false,
      skill_name: "",
      message: errorMessage,
    };
  }

  return response.json();
}
