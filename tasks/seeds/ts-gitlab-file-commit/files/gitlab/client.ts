// Minimal GitLab REST v4 client used by our config-sync tooling.
// Auth is the documented PRIVATE-TOKEN header; namespaced project paths and
// repository file paths travel URL-encoded (/ becomes %2F) per the docs.

export class GitLabApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "GitLabApiError";
    this.status = status;
  }
}

/** Encode one path component (project path or in-repo file path). */
export function encodePathComponent(path: string): string {
  return encodeURIComponent(path);
}

export interface RepositoryFile {
  file_name: string;
  file_path: string;
  size: number;
  encoding: string;
  content: string;
  ref: string;
  blob_id: string;
  commit_id: string;
  last_commit_id: string;
}

function describeError(status: number, payload: unknown): string {
  if (payload && typeof payload === "object") {
    const message = (payload as { message?: unknown; error?: unknown }).message ??
      (payload as { error?: unknown }).error;
    if (typeof message === "string") return message;
    if (message !== undefined) return JSON.stringify(message);
  }
  return `HTTP ${status}`;
}

export class GitLabClient {
  readonly baseUrl: string;
  private readonly token: string;

  constructor(baseUrl: string, token: string) {
    this.baseUrl = baseUrl.replace(/\/+$/, "");
    this.token = token;
  }

  /** One authenticated JSON round trip against /api/v4. */
  async requestJson(
    method: string,
    pathAndQuery: string,
    body?: unknown,
  ): Promise<any> {
    const headers: Record<string, string> = {
      "PRIVATE-TOKEN": this.token,
      Accept: "application/json",
    };
    const init: RequestInit = { method, headers };
    if (body !== undefined) {
      headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    const response = await fetch(this.baseUrl + pathAndQuery, init);
    const text = await response.text();
    let payload: unknown = null;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = text;
      }
    }
    if (!response.ok) {
      throw new GitLabApiError(response.status, describeError(response.status, payload));
    }
    return payload;
  }

  /** GET /projects/:id/repository/files/:file_path?ref=... */
  async getFile(
    projectPath: string,
    filePath: string,
    ref: string,
  ): Promise<RepositoryFile> {
    const path =
      `/api/v4/projects/${encodePathComponent(projectPath)}` +
      `/repository/files/${encodePathComponent(filePath)}` +
      `?ref=${encodeURIComponent(ref)}`;
    return (await this.requestJson("GET", path)) as RepositoryFile;
  }
}
