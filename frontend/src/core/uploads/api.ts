/**
 * API functions for file uploads
 */

import { fetch } from "../api/fetcher";
import { getBackendBaseURL } from "../config";

export interface UploadedFileInfo {
  filename: string;
  size: number;
  path: string;
  virtual_path: string;
  artifact_url: string;
  extension?: string;
  modified?: number;
  markdown_file?: string;
  markdown_path?: string;
  markdown_virtual_path?: string;
  markdown_artifact_url?: string;
  ocr_file?: string;
  ocr_path?: string;
  ocr_virtual_path?: string;
  ocr_artifact_url?: string;
}

export interface UploadResponse {
  success: boolean;
  files: UploadedFileInfo[];
  message: string;
  skipped_files: string[];
}

export interface ListFilesResponse {
  files: UploadedFileInfo[];
  count: number;
}

export class UploadRequestError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly threadId: string,
  ) {
    super(message);
    this.name = "UploadRequestError";
  }
}

export function isStaleThreadUploadError(error: unknown) {
  if (!(error instanceof Error)) {
    return false;
  }
  const status = Reflect.get(error, "status");
  return (
    status === 404 && /^Thread\s+\S+\s+not found$/.test(error.message.trim())
  );
}

async function readErrorDetail(
  response: Response,
  fallback: string,
): Promise<string> {
  const error = await response.json().catch(() => ({ detail: fallback }));
  return typeof error.detail === "string" ? error.detail : fallback;
}

function uploadsURL(threadId: string, suffix = ""): string {
  return `${getBackendBaseURL()}/api/threads/${encodeURIComponent(threadId)}/uploads${suffix}`;
}

/**
 * Upload files to a thread
 */
export async function uploadFiles(
  threadId: string,
  files: File[],
): Promise<UploadResponse> {
  const formData = new FormData();

  files.forEach((file) => {
    formData.append("files", file);
  });

  const response = await fetch(uploadsURL(threadId), {
    method: "POST",
    body: formData,
    timeoutMs: null,
  });

  if (!response.ok) {
    throw new UploadRequestError(
      await readErrorDetail(response, "Upload failed"),
      response.status,
      threadId,
    );
  }

  return response.json();
}

/**
 * List all uploaded files for a thread
 */
export async function listUploadedFiles(
  threadId: string,
): Promise<ListFilesResponse> {
  const response = await fetch(uploadsURL(threadId, "/list"));

  if (!response.ok) {
    throw new Error(
      await readErrorDetail(response, "Failed to list uploaded files"),
    );
  }

  return response.json();
}

/**
 * Delete an uploaded file
 */
export async function deleteUploadedFile(
  threadId: string,
  filename: string,
): Promise<{ success: boolean; message: string }> {
  const response = await fetch(
    uploadsURL(threadId, `/${encodeURIComponent(filename)}`),
    {
      method: "DELETE",
    },
  );

  if (!response.ok) {
    throw new Error(await readErrorDetail(response, "Failed to delete file"));
  }

  return response.json();
}
