// Stands in for the Workers runtime's `cloudflare:email` module under Node: the message keeps what it was built with.
export class EmailMessage {
  constructor(from, to, raw) {
    this.from = from;
    this.to = to;
    this.raw = raw;
  }
}
