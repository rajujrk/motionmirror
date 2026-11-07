export type UploadForm = { url: string; fields: Record<string, string> };

/** A multipart POST straight to storage. XMLHttpRequest because fetch has no upload progress. */
export function uploadFile(
  form: UploadForm,
  file: File,
  onProgress: (fraction: number) => void,
  signal?: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const body = new FormData();
    for (const [name, value] of Object.entries(form.fields)) body.append(name, value);
    body.append("file", file);

    const request = new XMLHttpRequest();
    request.open("POST", form.url);
    request.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    };
    request.onload = () =>
      request.status >= 200 && request.status < 300
        ? resolve()
        : reject(new Error("The storage service refused this file. Check its size and type."));
    request.onerror = () => reject(new Error("The upload was interrupted. Check your connection and try again."));
    request.onabort = () => reject(new DOMException("Upload cancelled", "AbortError"));
    signal?.addEventListener("abort", () => request.abort());
    request.send(body);
  });
}
