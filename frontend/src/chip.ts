// Icon chips: a stable colour per holding (hash of its id) from the mock's palette.
// Every colour keeps >= 4.5:1 against the near-black chip text (#0B0D17); violet is the lightened #9277FF.
const PALETTE = ["#9277FF", "#22D3EE", "#F472B6", "#F59E0B", "#10B981", "#3B82F6", "#EF4444", "#8B5CF6", "#14B8A6", "#EC4899", "#EAB308", "#0EA5E9"] as const;

export function chipColor(id: string): string {
  let h = 5381;
  for (let i = 0; i < id.length; i++) h = ((h << 5) + h + id.charCodeAt(i)) >>> 0;
  return PALETTE[h % PALETTE.length] as string;
}

export function chipText(id: string): string {
  return id.replace(/[-_.\s]+$/g, "").slice(0, 4).toUpperCase();
}
