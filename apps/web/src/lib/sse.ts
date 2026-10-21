export interface ServerEvent {
  id: string;
  event: string;
  data: string;
}

/** Incremental SSE framing. The caller decodes UTF-8 with a streaming TextDecoder. */
export class SseDecoder {
  private line = "";
  private data: string[] = [];
  private type = "";
  private id = "";
  private pendingCR = false;
  private size = 0;

  constructor(private readonly limit = 262_144) {}

  push(chunk: string): ServerEvent[] {
    const events: ServerEvent[] = [];
    for (const character of chunk) {
      if (this.pendingCR) {
        this.pendingCR = false;
        if (character === "\n") continue;
      }
      if (character === "\r" || character === "\n") {
        this.consumeLine(events);
        this.pendingCR = character === "\r";
      } else {
        this.line += character;
        this.size += character.length;
        if (this.size > this.limit) throw new Error("实时进度内容超过可读取范围。");
      }
    }
    return events;
  }

  private consumeLine(events: ServerEvent[]) {
    const line = this.line;
    this.line = "";
    if (line === "") {
      if (this.data.length) events.push({ id: this.id, event: this.type || "message", data: this.data.join("\n") });
      this.data = [];
      this.type = "";
      this.size = 0;
      return;
    }
    if (line.startsWith(":")) { this.size -= line.length; return; }
    const separator = line.indexOf(":");
    const field = separator < 0 ? line : line.slice(0, separator);
    let value = separator < 0 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "data") this.data.push(value);
    else if (field === "event") this.type = value;
    else if (field === "id" && !value.includes("\0")) this.id = value;
  }
}
