# In-app support

`POST /v1/support/issues` accepts bug reports and feature requests from guests and
signed-in readers. It creates a public issue in the configured repository (default
`PapyrusReader/client`). The request
is multipart: `payload` contains the JSON support fields and optional repeated
`attachments` parts contain images or videos.

Set `SUPPORT_GITHUB_TOKEN` in the server environment (`production.env` for Compose)
to a fine-grained PAT restricted to that repository with **Issues: read and write**.
Also grant organization **Projects: read and write** for the Papyrus project.
The token owner must also have repository write access for media uploads. GitHub
App installation tokens cannot upload attachments. Keep the token out of client
builds. Without it, the endpoint returns 503.

Routing is controlled by the `SUPPORT_GITHUB_*` environment variables shown in
`.env.example` and `deploy/production.env.example`: repository, assignee, project
owner/number, labels, issue types, and project field names/values. Set overrides in
the server's ignored `.env` or deployment `production.env`. These routing values
are ordinary configuration. Only `SUPPORT_GITHUB_TOKEN` is a secret. None of these
settings can be overridden by a support request.

By default, bug reports receive the `bug` label and native `Bug` issue type. Feature requests
receive `enhancement` and `Feature`. Both are assigned to `Eoic` and added to the
Papyrus project (#1) with Status `Backlog` and Kind `Task`. Priority, milestone,
and requirement links are left for triage. Project access and field options are
checked before uploads or issue creation. The server sends the confirmed issue
response before applying project updates in a background task. Slow project updates
cannot delay that response. If updates fail, the server logs the issue number for
manual reconciliation. Background routing is not persisted or retried, so an abrupt
server shutdown can leave project metadata incomplete.

Reading a public project and its `viewerCanUpdate` value only confirms visibility
and the token owner's role. It does not prove the token has Projects write access.
A `FORBIDDEN` project mutation is logged with the issue number and a permission
hint. Grant organization Projects read/write to the configured token, then add
any already-created reports to the project without resubmitting them.

The form accepts up to five PNG, JPEG, GIF, WebP, MP4, MOV, or WebM files, each up to
10 MiB. The server checks sizes and media signatures before contacting GitHub.
The multipart request is bounded to 50 MiB plus 64 KiB of form overhead, including
chunked requests. Media is uploaded through the same native GitHub attachment
endpoint used by [GitHub CLI](https://github.com/cli/cli/blob/v2.102.0/internal/attachments/client.go),
then embedded in the issue. Original filenames are replaced with generic names.
Nothing is stored in the reader's library or exposed through library media routes.

Submissions are limited to three per hour and ten per day per client IP using the
existing process-local limiter. Configure trusted proxy forwarding so requests
retain their client IP. Multiple API workers need a shared limiter or equivalent
edge limits before increasing deployment scale.

Each GitHub request has a 30-second socket timeout. The client allows four minutes
for submission and never retries automatically. A failed submission preserves the
form and selected files. An attachment failure prevents issue creation, but files
already accepted by GitHub may remain public even if a later step fails. GitHub
does not provide an attachment deletion API. An issue-creation timeout may mean
GitHub created the issue without the client receiving confirmation.

Tests mock GitHub and never create public issues or upload real attachments.
