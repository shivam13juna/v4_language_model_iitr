# GitHub MCP Server with Claude Code

A practical setup and usage guide for connecting GitHub's official MCP server to Claude Code.

> Last verified: 4 July 2026  
> Recommended setup: GitHub's hosted MCP server over Streamable HTTP.

## What this connection provides

After the GitHub MCP server is connected, Claude Code can use GitHub capabilities such as:

- reading repositories and files;
- searching code;
- listing and managing issues;
- reading and reviewing pull requests;
- checking commits and GitHub Actions;
- performing write operations when the token and Claude Code permissions allow them.

The architecture is:

```text
Claude Code
    |
    | MCP over Streamable HTTP
    v
GitHub hosted MCP server
    |
    | GitHub APIs
    v
Repositories, issues, pull requests, actions, and other GitHub data
```

## 1. Prerequisites

You need:

- Claude Code installed;
- a GitHub account;
- a GitHub Personal Access Token for the hosted setup;
- access to the repositories you want Claude to use.

Check Claude Code:

```bash
claude --version
```

Run all `claude mcp ...` commands in your normal terminal, not inside the Claude Code chat interface.

## 2. Create a GitHub Personal Access Token

In GitHub:

```text
Profile picture
→ Settings
→ Developer settings
→ Personal access tokens
→ Fine-grained tokens
→ Generate new token
```

Recommended configuration:

1. Give the token a clear name, such as `claude-code-github-mcp`.
2. Set an expiration date.
3. Select only the repositories Claude needs.
4. Start with read-only permissions.
5. Add write permissions only when you intentionally want Claude to create or modify GitHub objects.

Typical read-only permissions depend on your intended tasks, but may include:

- Contents: Read
- Issues: Read
- Pull requests: Read
- Actions: Read
- Metadata: Read

For creating or editing issues and pull requests, grant the corresponding write permission.

Important:

- A token can never grant more access than your GitHub account already has.
- An organization may require approval before a fine-grained token can access its repositories.
- Treat a PAT exactly like a password.
- Never commit the PAT to Git.

## 3. Store the token safely

### Option A: `.env` file

Create `.env` in the project directory:

```bash
printf 'GITHUB_PAT=YOUR_GITHUB_PAT\n' > .env
```

Prevent it from being committed:

```bash
printf '\n.env\n.mcp.json\n' >> .gitignore
```

Load it into the current terminal session:

```bash
export GITHUB_PAT="$(grep '^GITHUB_PAT=' .env | cut -d '=' -f2-)"
```

Check that it is present without printing it:

```bash
test -n "$GITHUB_PAT" && echo "GITHUB_PAT is set"
```

### Option B: enter it interactively on macOS/Zsh

```zsh
read -s "GITHUB_PAT?Paste your GitHub PAT: "
export GITHUB_PAT
echo
```

The token is hidden while being entered.

## 4. Add GitHub's hosted MCP server

For Claude Code 2.1.1 and newer:

```bash
claude mcp add-json github \
  '{"type":"http","url":"https://api.githubcopilot.com/mcp","headers":{"Authorization":"Bearer '"$GITHUB_PAT"'"}}'
```

Meaning:

| Part | Purpose |
|---|---|
| `claude mcp add-json` | Registers an MCP server from JSON configuration |
| `github` | Local name for this server |
| `"type":"http"` | Uses Streamable HTTP |
| `url` | GitHub's hosted MCP endpoint |
| `Authorization` | Authenticates using your PAT |

The shell expands `$GITHUB_PAT` before saving the configuration. Therefore, assume the resulting local MCP configuration contains a sensitive credential.

Restart Claude Code after adding the server.

## 5. Choose the configuration scope

The default scope is `local`.

| Scope | Effect |
|---|---|
| `local` | Available only to you in the current project |
| `project` | Shared through the project's `.mcp.json` |
| `user` | Available to you across all projects |

For an initial setup, use the default local scope.

To make it available in every project:

```bash
claude mcp remove github

claude mcp add-json github \
  '{"type":"http","url":"https://api.githubcopilot.com/mcp","headers":{"Authorization":"Bearer '"$GITHUB_PAT"'"}}' \
  --scope user
```

Do not put a literal PAT in a shared, committed `.mcp.json`.

## 6. Verify the connection

List configured MCP servers:

```bash
claude mcp list
```

Inspect the GitHub server configuration:

```bash
claude mcp get github
```

Start Claude Code:

```bash
claude
```

Inside Claude Code, run:

```text
/mcp
```

The `/mcp` panel shows connection status and the number of tools exposed by each connected server.

If a project-scoped server is shown as `Pending approval`, open Claude Code in that project and approve the workspace/server when prompted.

## 7. Perform a safe read-only test

Inside Claude Code:

```text
Using the GitHub MCP server, list the five most recently updated open
issues in OWNER/REPOSITORY.

Include:
- issue number
- title
- URL

Do not modify anything.
```

Another example:

```text
Using the GitHub MCP server, show the open pull requests in
OWNER/REPOSITORY.

For each pull request, include:
- number
- title
- author
- review status

Do not create comments or make changes.
```

Replace `OWNER/REPOSITORY` with a full repository identifier, for example:

```text
github/github-mcp-server
```

## 8. Use GitHub MCP prompts

MCP prompts are server-provided workflow templates. They are different from tools:

```text
Prompt = instructions for how to carry out a workflow
Tool   = an operation Claude can actually call
```

### Discover prompts

Inside Claude Code, type:

```text
/
```

Search for:

```text
mcp__github__
```

MCP prompts use this command format:

```text
/mcp__SERVER_NAME__PROMPT_NAME
```

For example:

```text
/mcp__github__AssignCodingAgent
```

Prompt arguments are written after the command:

```text
/mcp__github__some_prompt argument1 "argument containing spaces"
```

Claude Code:

1. discovers prompts from the server;
2. calls `prompts/get` when you select one;
3. injects the returned messages into the conversation;
4. lets Claude follow that workflow and call tools.

### Current GitHub MCP prompt examples

The current open-source GitHub MCP server registers these two prompts:

```text
AssignCodingAgent
issue_to_fix_workflow
```

The live hosted server is authoritative; available prompts can change and may depend on enabled toolsets.

#### `AssignCodingAgent`

Use the slash menu to select it and provide the requested repository identifier, normally in this form:

```text
OWNER/REPOSITORY
```

Example:

```text
/mcp__github__AssignCodingAgent github/github-mcp-server
```

This workflow can evaluate issues and assign suitable ones to GitHub's Copilot coding agent. It can cause real write operations.

For a read-only evaluation instead, use an ordinary request:

```text
Using GitHub MCP, list the ten latest issues in OWNER/REPOSITORY and
evaluate which ones appear suitable for a coding agent.

Do not assign anything and do not modify the repository.
```

#### `issue_to_fix_workflow`

The prompt currently defines these arguments:

```text
owner        required
repo         required
title        required
description  required
labels       optional
assignees    optional
```

Example:

```text
/mcp__github__issue_to_fix_workflow \
  OWNER \
  REPOSITORY \
  "Fix login redirect" \
  "After successful authentication, redirect users to /dashboard instead of /404." \
  "bug,authentication" \
  "USERNAME"
```

This workflow is designed to:

1. create an issue;
2. assign it to the Copilot coding agent;
3. monitor creation of a corresponding pull request.

It performs real GitHub write operations when permitted.

## 9. List GitHub MCP tools, prompts, and resources

### Connection status and tool count

Inside Claude Code:

```text
/mcp
```

From the shell:

```bash
claude mcp list
claude mcp get github
```

These commands show configuration and connection information. They are not a full protocol-level dump of every tool schema.

### Ask Claude to summarize available tools

Inside Claude Code:

```text
List the GitHub MCP tools currently available in this session.

Group them by:
- repositories
- code search
- issues
- pull requests
- actions
- users
- security

For each tool, show its exact tool name and a one-line description.
Do not execute any GitHub operation.
```

Claude Code uses deferred MCP tool search by default, so tool definitions may be discovered only when needed rather than all being loaded into context at startup.

### Discover prompts

Type:

```text
/
```

Then search:

```text
mcp__github__
```

### Discover resources

Type:

```text
@
```

MCP resources appear alongside files in autocomplete.

The general resource reference format is:

```text
@server:protocol://resource/path
```

Example format from Claude Code documentation:

```text
@github:issue://123
```

If no GitHub entries appear after typing `@`, the connected server may currently expose no browsable resources. Tools, prompts, and resources are independent MCP capabilities.

## 10. Inspect the protocol directly with MCP Inspector

For an exact view of:

- `tools/list`;
- `resources/list`;
- `prompts/list`;
- tool input schemas;
- prompt parameters;
- resource metadata;

launch MCP Inspector:

```bash
npx @modelcontextprotocol/inspector
```

In the browser UI:

```text
Transport: Streamable HTTP
URL: https://api.githubcopilot.com/mcp
```

Add authentication:

```text
Authorization: Bearer YOUR_GITHUB_PAT
```

Then connect and inspect the separate tabs:

```text
Tools
Resources
Prompts
```

Do not expose the Inspector proxy to an untrusted network, and do not show or record your PAT.

Note: MCP Inspector and remote authentication behavior can change independently. If direct inspection fails while Claude Code works, use Claude Code's `/mcp`, `/`, and `@` interfaces as the source of truth for that client session.

## 11. Manage or remove the server

List servers:

```bash
claude mcp list
```

Inspect GitHub:

```bash
claude mcp get github
```

Remove GitHub:

```bash
claude mcp remove github
```

There is no general `claude mcp update` command. To change a configuration, remove the server and add it again.

## 12. Alternative: local Docker server with browser OAuth

Prerequisite: Docker Desktop must be installed and running.

This runs the official GitHub MCP server locally and opens a browser login flow:

```bash
claude mcp add github \
  -e GITHUB_OAUTH_CALLBACK_PORT=8085 \
  -- docker run -i --rm \
  -p 127.0.0.1:8085:8085 \
  -e GITHUB_OAUTH_CALLBACK_PORT \
  ghcr.io/github/github-mcp-server
```

Then restart Claude Code and verify:

```bash
claude mcp list
```

Inside Claude Code:

```text
/mcp
```

## 13. Alternative: local Docker server with a PAT

```bash
claude mcp add github \
  -e GITHUB_PERSONAL_ACCESS_TOKEN="$GITHUB_PAT" \
  -- docker run -i --rm \
  -e GITHUB_PERSONAL_ACCESS_TOKEN \
  ghcr.io/github/github-mcp-server
```

Verify:

```bash
claude mcp list
claude mcp get github
```

## 14. Alternative: local native binary

After downloading the official `github-mcp-server` binary and placing it on `PATH`:

```bash
claude mcp add-json github \
  '{"command":"github-mcp-server","args":["stdio"],"env":{"GITHUB_PERSONAL_ACCESS_TOKEN":"YOUR_GITHUB_PAT"}}'
```

Avoid placing a literal token into shell history. Prefer a secure environment or credential-management method.

## 15. Legacy command for older Claude Code versions

For Claude Code 2.1.0 or earlier:

```bash
claude mcp add --transport http \
  github \
  https://api.githubcopilot.com/mcp \
  -H "Authorization: Bearer YOUR_GITHUB_PAT"
```

With the environment variable:

```bash
claude mcp add --transport http \
  github \
  https://api.githubcopilot.com/mcp \
  -H "Authorization: Bearer $GITHUB_PAT"
```

For current Claude Code versions, prefer `claude mcp add-json`.

## 16. Troubleshooting

### `Unknown command: /mcp_github_some_prompt`

The correct MCP prompt format uses double underscores:

```text
/mcp__github__PROMPT_NAME
```

The prompt name must actually be exposed by the connected server. Type `/` and select it from autocomplete rather than guessing.

### `401 Unauthorized`

Likely causes:

- invalid token;
- expired token;
- revoked token;
- incorrectly constructed Authorization header.

Fix:

```bash
claude mcp remove github
```

Correct the PAT and add the server again.

### `403 Forbidden`

The token is valid but does not have sufficient access.

Check:

- selected repositories;
- token permissions;
- organization approval;
- SSO requirements;
- whether your GitHub account itself has access.

### `Pending approval`

Run Claude Code interactively in the project:

```bash
claude
```

Approve the workspace and MCP server when prompted.

Then check:

```text
/mcp
```

### Tools appear but prompts or resources do not

This is valid MCP behavior.

A server can expose:

```text
many tools
few prompts
zero resources
```

The live discovery result in Claude Code is the authority.

### Server is configured but not connected

Run:

```bash
claude mcp list
claude mcp get github
```

Restart Claude Code and inspect:

```text
/mcp
```

If necessary:

```bash
claude mcp remove github
```

Then add it again.

### The command hangs on a prompt workflow

Some workflows make several tool calls or wait for GitHub-side activity.

You can interrupt Claude Code with:

```text
Esc
```

For testing, prefer an explicitly read-only natural-language request.

## 17. Security checklist

Before allowing write operations:

- use a fine-grained token;
- restrict it to specific repositories;
- grant the minimum permissions required;
- set an expiration date;
- keep `.env` out of Git;
- inspect tool calls before approving them;
- use a disposable repository for experiments;
- state `Do not modify anything` for read-only tests;
- revoke the token immediately if it is exposed.

Prompt instructions are not a security boundary. Actual protection should come from:

- token permissions;
- repository permissions;
- Claude Code tool approvals;
- server-side validation.

## 18. Command cheat sheet

### Terminal commands

```bash
claude --version

claude mcp add-json github \
  '{"type":"http","url":"https://api.githubcopilot.com/mcp","headers":{"Authorization":"Bearer '"$GITHUB_PAT"'"}}'

claude mcp list
claude mcp get github
claude mcp remove github

claude

npx @modelcontextprotocol/inspector
```

### Inside Claude Code

```text
/mcp
```

Check MCP server status and tool counts.

```text
/
```

Browse commands and MCP prompts.

```text
@
```

Browse files and MCP resources.

```text
/mcp__github__AssignCodingAgent OWNER/REPOSITORY
```

Invoke the GitHub coding-agent prompt, when exposed.

```text
/mcp__github__issue_to_fix_workflow OWNER REPO "TITLE" "DESCRIPTION"
```

Invoke the issue-to-fix workflow, when exposed.

## Official references

- GitHub MCP Server — Claude installation guide:  
  https://github.com/github/github-mcp-server/blob/main/docs/installation-guides/install-claude.md

- GitHub MCP Server repository:  
  https://github.com/github/github-mcp-server

- Claude Code MCP documentation:  
  https://code.claude.com/docs/en/mcp

- GitHub Personal Access Token documentation:  
  https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/managing-your-personal-access-tokens

- MCP Inspector documentation:  
  https://modelcontextprotocol.io/docs/tools/inspector

- GitHub MCP prompt registration source:  
  https://github.com/github/github-mcp-server/blob/main/pkg/github/prompts.go
