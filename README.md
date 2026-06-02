<img width="1500" height="500" alt="Centaur banner" src="https://github.com/user-attachments/assets/cc85cdb1-5a72-4eb2-ba1b-2e0a8fbbf691" />

<h4 align="center">
    Example organization overlay for Centaur.
</h4>

<p align="center">
  Package your tools, workflows, skills, and sandbox guidance as a reusable
  overlay image.
</p>

<p align="center">
  <a href="#use-this-template">Use This Template</a> •
  <a href="#what-you-customize">What You Customize</a> •
  <a href="#build-the-overlay-image">Build</a> •
  <a href="#verify-in-a-running-deployment">Verify</a>
</p>

## Overview

`centaur-acme` is a public, forkable template for creating an organization
overlay for [Centaur](https://github.com/paradigmxyz/centaur). It is
intentionally small and free of private data. Use it as the starting point for
shipping org-specific capabilities without forking the core platform.

The overlay image is copied into Centaur at runtime:

```text
centaur-acme repo
    |
    v
overlay image
    |
    +-- /app/overlay/org in the API
    +-- /home/agent/overlay/org in sandbox pods
```

## Use This Template

1. Click **Use this template** in GitHub, or fork the repo if you want to keep a
   visible upstream relationship.
2. Rename ACME examples to your organization or team name.
3. Replace the toy CRM tool with one small real integration.
4. Update the sandbox prompt and skill to match the behavior your agents should
   follow.
5. Build and publish the overlay image, then point your Centaur Helm values at
   that image.

Keep credentials out of this repository. Tools should request secrets through
Centaur's secret system instead of committing values or `.env` files.

## What You Customize

The template demonstrates the extension points an organization normally owns:

- `tools/` for API-discovered tools
- `workflows/` for durable workflows
- `.agents/skills/` for sandbox-loaded skills
- `services/sandbox/SYSTEM_PROMPT.md` for organization-specific agent guidance

## Repository Map

```text
.
├── .agents/skills/acme-support/     # sandbox skill loaded with the overlay
├── services/sandbox/SYSTEM_PROMPT.md
├── tools/acme_crm/                  # packaged Python CLI tool with sample data
├── tools/acme_go/                   # minimal Go CLI tool
├── tools/acme_rust/                 # minimal Rust CLI tool
├── workflows/daily_acme_brief.py    # example durable workflow
├── tests/
└── Dockerfile                       # copies the overlay to /overlay
```

## Build the overlay image

```bash
docker build -t ghcr.io/<org>/<overlay-repo>:local .
```

The image copies this repository to `/overlay`. Centaur's Helm chart mounts that
path at `/app/overlay/org` in the API and `/home/agent/overlay/org` in sandbox
pods.

## Use with Helm

```yaml
overlay:
  image:
    repository: ghcr.io/<org>/<overlay-repo>
    tag: sha-0000000
    pullPolicy: IfNotPresent
    sourcePath: /overlay
```

For the full GitOps example, pair this repo with
[`centaur-acme-infra`](https://github.com/paradigmxyz/centaur-acme-infra).

## Included examples

`tools/acme_crm` is a packaged Python CLI tool with no external credentials.
`tools/acme_rust` and `tools/acme_go` are tiny compiled CLI examples that prove
an overlay can ship source-built tools across the supported runtimes.

`workflows/daily_acme_brief.py` is a minimal recurring workflow that asks an
agent for a daily operating summary.

`.agents/skills/acme-support/SKILL.md` is a sandbox skill that demonstrates how
ACME-specific playbooks are packaged.

`services/sandbox/SYSTEM_PROMPT.md` is appended to the base sandbox prompt when
the overlay is mounted.

## Verify in a running deployment

From the API pod:

```bash
echo "$TOOL_DIRS"
echo "$WORKFLOW_DIRS"
ls -la /app/overlay/org
```

From a sandbox:

```bash
echo "$CENTAUR_OVERLAY_DIR"
ls "$CENTAUR_OVERLAY_DIR"
ls "$CENTAUR_OVERLAY_DIR/.agents/skills"
```

## Local checks

```bash
uv run pytest
```
