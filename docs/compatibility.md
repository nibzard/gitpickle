# Compatibility and availability

This matrix describes the implemented development snapshot validated for T052.
It does not certify M3, M4, or a public release. GitPickle remains a working name.
“Local” means bounded fixtures on the development machine, not a full-size or fresh-machine result.

| Surface | Implemented behavior | Evidence and limits |
| --- | --- | --- |
| Packaging | Linux x86-64; pinned build targets glibc 2.36 or later | Host builds inherit host requirements. Other platforms are unverified. [Deployment](deployment.md) |
| Runtime | One executable embeds Python modules; native Git and Python remain required | Daemon uses Linux `prlimit`; LFS capture needs Git LFS; terminal interface needs curses. |
| Archive | Version 1, SHA-256 archive digests, SHA-1 and SHA-256 Git objects | Retained reader fixture and local recovery tests; unknown versions/encryption rejected. [Format](archive-format/README.md) |
| Management and JSON | Protocol and result version 1; capability negotiation | Unsupported versions and missing capabilities fail before dispatch. [Recovery compatibility](recovery.md#compatibility-policy-and-evidence) |
| R2 | Capture, durable jobs, readback, storage-only recovery | Actual bounded provider evidence; no enforced retention or conditional multipart claim. [R2 evidence](r2-conformance.md) |
| Amazon S3 | Capture and recovery adapters | Local adapter tests; live acceptance pending T054. |
| Backblaze B2 | Content-addressed publication and recovery adapter | Conditional writes remain unsupported. Deterministic fault and recovery checks pass; live acceptance pending T054. |
| GitHub | Authorized discovery, scheduled capture, Git and required LFS | Private GitHub/R2 fixture passes. Enabled wiki uses separate capture; live private GitHub wiki remains unverified. |
| Native Git HTTPS | Explicit enablement; mirror reads and hosted pushes | Git 2.47.1 local matrix; protocols 0/1/2, shallow/unshallow, empty repositories, SHA-1/SHA-256. [Hosting](git-hosting.md) |
| LFS hosting | Authenticated basic upload and download in hosted mode; downloads for mirrors | Real local Git LFS clients; durable upload and R2 recovery checks. Locking unavailable. |
| Git transfer bounds | Read requests up to 1 MiB; hosted pushes and LFS uploads up to 64 MiB; backend Git output up to 16 GiB | File-backed Git/LFS downloads pass above 64 MiB. Deadlines and staging disk still apply. No full-size performance claim. |
| Signed commits/tags | Stored Git objects | No signature verification or trust-policy claim. |
| Terminal interface | Optional keyboard interface, plain CLI alternatives | 404 registered entries without captured payload; bounded local navigation measurements. [TUI](tui.md) |
| Model Context Protocol (MCP) | Local stdio, seven constrained tools | Shared authorization; operator approval is outside agent tools. Remote MCP unavailable. [MCP](mcp.md) |
| VM/systemd | Bundle, user service, installation plan | Local lifecycle checks; integrated fresh-host evidence pending T055. |
| Container/Coolify | Pinned image and generated recipes | Local host-network recovery passes; default bridge DNS failed. Actual Coolify acceptance pending T055. |
| Private HTTPS/Tailscale | Configuration and deployment procedure | Local test certificate only; actual tailnet, routing, and certificate acceptance pending T055. |

## Unavailable capabilities

The daemon rejects pushes to protected mirrors. Explicit creation and conversion enable durable hosted pushes.
Native receive-pack preserves partial and atomic results. Local tests cover the response gate and mode isolation.
T037 retains the complete M4 compatibility, provider fault, and contention matrix. Signed push certificates remain unsupported.
Partial-clone filters, SSH Git transport, dumb HTTP, and server hooks are unavailable.
Submodule references are Git data; capture does not fetch submodule repositories automatically.

Issues, pull requests, discussions, release attachments, packages, and account settings are later connectors and are not included.
Client-side archive encryption, retention deletion, garbage collection, second-copy replication, and remote MCP are unavailable.
Automatic multi-writer takeover and active-active operation are unavailable.
Cloudflare Workers is not a deployment target.

The optional terminal interface is implemented. Optional future capabilities above remain unavailable even when proposed in `gitpickle.md`.
See [release evidence](release/README.md) for measured results and final gates.
