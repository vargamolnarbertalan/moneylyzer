// Files attached to an AI analyst question: read in the browser, sent with that one question, never stored
// (a saved conversation keeps only their names and sizes). The server sniffs and converts them per model.

export const MAX_FILES = 5;
export const MAX_TOTAL_BYTES = 6 * 1024 * 1024; // the server's limit too (fits every model route)

export interface PendingFile {
  id: string;
  name: string;
  size: number;
  data: string; // base64, without the data: prefix
}

export function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

/** Add files to those already chosen, within the limits; the ones that don't fit are named in `error`. */
export async function addFiles(chosen: File[], current: PendingFile[]): Promise<{ files: PendingFile[]; error: string | null }> {
  const files = [...current];
  const refused: string[] = [];
  let total = files.reduce((sum, f) => sum + f.size, 0);
  for (const file of chosen) {
    if (files.length >= MAX_FILES) {
      refused.push(`${file.name} (at most ${MAX_FILES} files per question)`);
      continue;
    }
    if (total + file.size > MAX_TOTAL_BYTES) {
      refused.push(`${file.name} (over ${fileSize(MAX_TOTAL_BYTES)} in total)`);
      continue;
    }
    try {
      const data = await readBase64(file);
      files.push({ id: `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`, name: file.name || "file", size: file.size, data });
      total += file.size;
    } catch {
      refused.push(`${file.name} (could not be read)`);
    }
  }
  return { files, error: refused.length ? `Not attached: ${refused.join("; ")}.` : null };
}
