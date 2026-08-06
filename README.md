# KAIOS — Product Intelligence and AI Business Operations

KAIOS is a workspace-aware AI business operating system focused on
Etsy and print-on-demand Product Intelligence. It accepts a human business
request, routes structured work through a CEO Orchestrator and specialist
agent, persists the audit trail, and returns recommendations for human review.

Phase 2 adds an official, read-only Etsy research source while preserving the
offline Phase 1 demonstration. Analysis remains deterministic and free by
default: live evidence is evaluated by `RulesModelProvider`, not a paid model.

## Phase 1 architecture

KAIOS uses the following hierarchy:

```text
Workspace
└── Agent
    └── Task
        └── Result
            └── Decision / Approval
```

- **Workspace:** isolates one business and all of its tasks, results, events,
  proposals, approvals, and decisions. The default workspace is
  `print-on-demand`.
- **CEO Orchestrator:** accepts a structured human request, creates a parent CEO
  task and child specialist task, routes the work, and records the resulting
  recommendation.
- **Product Intelligence Agent:** wraps the existing extractor, analyzer, and
  reporter workflow to research product opportunities and return structured
  evidence, metrics, recommendations, and report paths.
- **Store Operations, Marketing, and Finance:** registered capability shells
  only. They contain no operational business logic in Phase 1.
- **Repositories:** persistence-neutral interfaces with in-memory and SQLite
  implementations.
- **Model providers:** pluggable rules, fake, and optional LiteLLM adapters. The
  Phase 1 CLI uses `RulesModelProvider` by default.

## Safety boundaries

KAIOS uses a default-deny approval workflow for risky proposed actions.
Publishing, spending, advertising, external messaging, financial transfers,
deletion, public actions, and irreversible actions require explicit human
approval of the exact payload hash.

Phase 1 execution is **simulation only**:

- it does not publish Etsy or Shopify listings;
- it does not spend or transfer money;
- it does not start advertisements;
- it does not send external messages;
- it does not modify marketplace or supplier accounts;
- the CEO Orchestrator cannot approve its own proposal;
- approval can produce only a simulated execution audit event.

## Evidence labels

KAIOS keeps evidence origin explicit:

- `LIVE` means a successful response from an allowlisted official marketplace
  API.
- `FALLBACK` is reserved for explicitly identified non-live evidence. KAIOS does
  not silently substitute fallback evidence for a failed Etsy request.
- `MOCK / OFFLINE DEMO` means deterministic development fixtures.

Public favourites, reviews, and shop activity are popularity indicators. They
are not presented as verified listing sales.

## Offline evidence warning

The bundled demonstration uses deterministic fixture evidence labelled:

```text
MOCK / OFFLINE DEMO
```

This evidence is **not live Etsy research or marketplace validation**. It is
provided only to develop and verify the architecture without network or paid AI
usage. Product decisions still require live evidence and human validation.

## Installation

Python 3.11 or newer is required.

```bash
git clone https://github.com/KyleSolutions100/kaios-product-intelligence.git
cd kaios-product-intelligence
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
```

The editable installation provides the `kaios` command. The equivalent module
form is `python -m kaios.main`.

No `.env` file or AI API key is required for the offline workflow.

## Official read-only Etsy research

Live research uses only:

```text
GET https://openapi.etsy.com/v3/application/listings/active
```

KAIOS sends search keywords and a result limit, and requests public `Shop` and
`Images` inclusions where Etsy makes them available. Image URLs are retained as
references only; KAIOS does not download the images.

Register an Etsy application and obtain access appropriate for public
marketplace research. Put the keystring/shared-secret value in your local
environment or ignored `.env` file:

```bash
export ETSY_API_KEY='YOUR_KEYSTRING:YOUR_SHARED_SECRET'
```

Never commit this value. The provider does not include it in tasks, results,
reports, logs, or error messages.

Run official live research through the unchanged CEO workflow:

```bash
kaios research "funny dog owner t-shirt" --marketplace etsy --limit 5
```

For a manual opt-in smoke test against temporary local output:

```bash
ETSY_API_KEY="$ETSY_API_KEY" kaios research "teacher tote bag" \
  --database /tmp/kaios-live-smoke.db \
  --output /tmp/kaios-live-smoke-reports
```

This command is never run by the automated test suite. If Etsy returns `403`,
confirm that the application is approved for the official public listing-search
endpoint and that its permitted use covers product-research analytics. KAIOS
will not fall back to scraping, browser automation, or mock data labelled live.

## Offline demonstration

Create the default workspace, run Product Intelligence through the CEO, persist
the audit trail, and leave a risky demonstration action pending:

```bash
kaios demo --approval pending
```

Run the same demonstration and explicitly approve simulation-only execution:

```bash
kaios demo --approval approve
```

Other safe choices are:

```bash
kaios demo --approval none
kaios demo --approval reject
```

## Main CLI commands

```bash
# Workspaces
kaios workspace create my-business --name "My Business"
kaios workspace list

# Official read-only Product Intelligence request through the CEO
kaios research "funny dog owner t-shirt"
kaios research "wedding invitation" --limit 5 --output reports
kaios research "teacher tote bag" --config config.yaml.example

# Persisted tasks, results, recommendations, and decisions
kaios task list
kaios task show TASK_ID
kaios recommendations
kaios decision list

# Human approval workflow
kaios approval list
kaios approval show APPROVAL_ID
kaios approval approve APPROVAL_ID
kaios approval reject APPROVAL_ID
```

Workspace-scoped commands accept `--workspace`. Commands accept `--database`
to use a database other than the default.

### Research configuration compatibility

`kaios research --config PATH` supports:

- `marketplace`
- `search_limit` or the legacy `default_search_limit`
- `output_dir`
- `model_provider: rules`
- `agent_model_providers.product_intelligence: rules`

The CLI options `--marketplace`, `--limit`, and `--output` override configured
values. The CEO CLI rejects paid model providers rather than silently ignoring
them. The marketplace provider uses the network only for official read-only
research; `RulesModelProvider` itself remains offline.

## Local data

- SQLite database: `data/kaios.db`
- Generated reports: `reports/`

Database files, SQLite journals/WAL files, reports, `.env`, caches, virtual
environments, and generated packaging files are ignored by Git.

## Development and verification

Run all automated tests:

```bash
python -m pytest -q
```

Run the legacy mocked end-to-end workflow:

```bash
python scripts/e2e.py
```

Run the complete offline CEO workflow against temporary or explicit paths:

```bash
kaios demo --approval approve \
  --database /tmp/kaios-demo.db \
  --output /tmp/kaios-demo-reports
```

## Current limitations

- Demo evidence is deterministic fixture data, not live marketplace evidence.
- Live Etsy access requires an approved Etsy application and is subject to Etsy
  endpoint permissions and rate limits.
- Public fields differ by listing and application access. Missing prices, shop
  details, reviews, tags, or images remain unavailable rather than fabricated.
- Review counts may be shop-scoped. Favourites and shop transactions are
  popularity context, not verified sales for a listing.
- No page scraping or browser-automation fallback is implemented.
- Publishing is not implemented.
- Store Operations, Marketing, and Finance remain capability shells.
- Human identity is represented by the local `human_owner` actor; authenticated
  user accounts are future work.
- Scheduling, autonomous loops, dashboards, advertisements, messaging,
  supplier operations, and financial execution are not implemented.
- LiteLLM remains an opt-in adapter outside the default offline CEO CLI flow.
- Reports can be regenerated or overwritten; SQLite remains the authoritative
  Phase 1 task and audit store.
