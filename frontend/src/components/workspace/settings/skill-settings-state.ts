export function canManageSkills(
  systemRole: "admin" | "user" | null | undefined,
  staticWebsiteOnly: boolean,
) {
  return systemRole === "admin" && !staticWebsiteOnly;
}
