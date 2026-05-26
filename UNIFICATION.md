# Shared Unification Plan — Offline Swarm Compute Architecture

_Last updated: 2026-05-26_

This document is the shared cross-repo source of truth for aligning `auto-assist`, `Sophia`, and `auto-ingest` into one offline, Tailscale-first swarm compute architecture.

It records the design decisions that have now been made and should guide the next implementation pass.

Repos covered:

- `scottjoyner/auto-assist`
- `scottjoyner/Sophia`
- `scottjoyner/auto-ingest`

---

## 1. Primary architectural decision

AssistX is the **task-state authority**.

auto-ingest and Sophia may run jobs, maintain local outboxes, keep operational SQLite/cache state, and publish events, but they should not become independent long-term task authorities.

The unified model is:

```text
Sophia = voice/auth edge
AssistX = orchestration, task state, policy, command center, delegation
Neo4j main DB = unified historical/personal memory about Scott
auto-ingest = periodic historical memory ingestion and data enrichment
LM Studio / Hermes / opencode nodes = delegated execution workers
NAS/filesystem = binary artifact source of truth
Tailscale = default private network fabric
```

---

## 2. Database unification strategy

### 2.1 Current reality

The existing data is split historically:

1. The original / legacy Neo4j `neo4j` database contains the long-running historical graph nodes from auto-ingest and prior memory work.
2. Sophia initially wrote quick transcription / voice-related graph records into the `memory` database.
3. AssistX introduced the `assistx` database later as orchestration needs became clearer.

### 2.2 Working decision

Use the legacy/main `neo4j` database as the **unified Scott memory graph**.

Use the `assistx` database as the **orchestration/control-plane graph**.

Treat Sophia's `memory` database as a transitional or staging database until its voice/capture/auth data is either migrated, copied, or dual-written into the unified Scott memory graph.

### 2.3 Intended boundaries

#### `neo4j` database — unified Scott memory

This database should contain durable knowledge about Scott and his world:

- historical transcripts
- auto-ingest outputs
- dashcam/bodycam/audio-derived knowledge
- user preferences and opinions
- normalized entities
- speaker and voice identity history
- source media references
- vector embeddings for searchable memory
- facts, notes, observations, and long-term semantic memory

#### `assistx` database — orchestration state

This database should contain live and historical control-plane state:

- tasks
- dispatches
- agent runs
- tool calls
- approvals
- policy decisions
- swarm node registry
- model endpoint registry
- worker heartbeats
- execution artifacts as task outputs
- delegation trace state

#### `memory` database — transitional Sophia overlay

This database exists because Sophia started before AssistX orchestration was fully defined. Next steps should decide whether to:

1. migrate Sophia `memory` records into `neo4j`,
2. keep it as a voice staging database, or
3. dual-write new Sophia memory events into both `memory` and unified `neo4j` until migration is complete.

The preferred direction is to make long-term Scott memory searchable from the main `neo4j` database.

---

## 3. Vector embedding plan

Sophia voice/capture nodes do not yet have vector embeddings. That should be fixed as part of memory unification.

### 3.1 Embedding targets

Add embeddings for:

- Sophia transcriptions
- voice captures
- voice-auth decisions with transcript summaries
- auto-ingest transcripts
- cleaned dashcam/bodycam utterances
- long-form summaries
- extracted opinions/preferences
- memory facts
- source-backed observations

### 3.2 Music/noise filtering requirement

Many dashcam transcripts are not meaningful Scott memory. They may contain:

- songs playing in the car
- radio/audio bleed
- non-Scott voices
- background navigation prompts
- road noise artifacts
- low-confidence STT hallucinations

Before using dashcam transcripts as semantic memory, auto-ingest should classify transcript segments by likely value:

- `scott_speech`
- `passenger_speech`
- `music_or_media`
- `navigation_or_alert`
- `ambient_noise`
- `unknown_low_confidence`

Only higher-confidence memory-bearing segments should be promoted into long-term semantic memory without review. Lower-confidence or media-like segments can remain searchable as source records but should not become asserted facts about Scott.

---

## 4. auto-ingest role

auto-ingest is a **secondary historical-memory ingestion system**, not the real-time control path.

It should be periodically reviewed and run when new historical data has accumulated. Expected cadence is weekly or monthly depending on dashcam/audio/bodycam usage, not necessarily daily.

### 4.1 auto-ingest responsibilities

- ingest historical audio/video/transcript/metadata
- normalize source records
- extract useful context about Scott
- generate transcript and media artifacts
- classify memory-bearing content
- enrich the unified Neo4j memory graph
- provide search context for AssistX and Sophia
- preserve provenance back to source files

### 4.2 auto-ingest should not block real-time AssistX

The real-time path should be:

```text
voice/web command -> Sophia -> AssistX -> task/delegation -> execution -> Sophia/AssistX response
```

auto-ingest should run as a background enrichment system:

```text
weekly/monthly historical run -> normalize data -> enrich unified Neo4j memory -> improve future context retrieval
```

### 4.3 Review loop

After each auto-ingest batch, AssistX should be able to review:

- new source files detected
- new transcript volume
- new candidate memories
- low-confidence segments
- likely music/media segments
- new entities
- new opinions/preferences extracted
- failed or skipped files
- proposed memory promotions

The result should be a human-reviewable memory update summary.

---

## 5. Tailscale-first network model

All swarm nodes should join the Tailscale network.

Tailscale should be the default private connectivity layer for:

- AssistX API
- Sophia voice sidecar
- Neo4j access
- LM Studio / OpenAI-compatible endpoints
- Hermes agents
- opencode-capable machines
- auto-ingest workers
- NAS-adjacent services where applicable

LAN IPs can be preferred when clearly faster and reachable, but Tailscale names/IPs should be the default stable addressing layer.

### 5.1 Pi-hole / local DNS

Pi-hole can be updated with stable local names as needed.

Suggested aliases:

```text
assistx.local       -> AssistX control plane
neo4j.local         -> Neo4j host
sophia.local        -> Sophia voice sidecar
models.local        -> primary model endpoint
x1-370.local        -> x1-370
mini-pc-22.local    -> mini-pc-22
deathstar.local     -> deathstar-XPS-8920
```

If MagicDNS is sufficient, Pi-hole aliases can be optional. If multiple services move between hosts, Pi-hole aliases become useful stable service names.

---

## 6. Paperclip role

Paperclip is primarily for:

- delegation
- agent management
- human/project-management visibility
- mapping AssistX tasks into an external agent-management view

Paperclip should not be the core source of task truth. AssistX remains task-state authority.

Preferred model:

```text
AssistX Task = authoritative execution state
Paperclip Issue = optional mirror / delegation wrapper / human coordination object
```

If Paperclip is offline, AssistX should still be able to run local/offline tasks.

---

## 7. Cache and queue direction

Redis is not the long-term strategic decision yet.

Near-term:

- keep current Redis usage where it already works.
- do not let Redis become the source of task truth.
- use local outboxes for disconnected worker reliability.
- keep AssistX task state authoritative.

Future direction:

- evaluate adding FalkorDB as a graph/cache layer on top of or adjacent to Neo4j.
- use FalkorDB for fast cache/working-set graph operations if useful.
- keep Neo4j as the durable memory/provenance store unless a later migration decision changes that.

---

## 8. Swarm node roles

### 8.1 x1-370

Primary high-power machine and likely first production control-plane / knowledge host.

Expected responsibilities:

- central AssistX runtime candidate
- local knowledge access
- high-throughput orchestration
- LM Studio/OpenAI-compatible endpoint hosting
- access to the most complete local info
- heavy jobs when appropriate

### 8.2 deathstar-XPS-8920

Former primary machine and legacy auto-ingest host.

Expected responsibilities:

- continue selected legacy auto-ingest jobs until migrated
- run Hermes-side delegation agents
- run Qwen-class local model endpoints where practical
- provide continuity for existing data paths and historical jobs

### 8.3 mini-pc-22

Still early-stage. May migrate to Ubuntu-only.

Expected responsibilities:

- future always-on service host candidate
- lightweight AssistX/Sophia/worker sidecars
- monitoring, Pi-hole-adjacent utilities, or control-plane support
- model endpoint if resources allow

### 8.4 demo and demo-1

Powerful GPU machines with limited memory.

Expected responsibilities:

- fast token-generation delegation agents
- burst heavy lifting when memory footprint allows
- Qwen-class 9B model work
- first-choice delegation workers for fast local model responses

### 8.5 Low-power machines

Lowest-power machines should handle drafts and low-risk, low-compute tasks where possible.

Examples:

- draft generation
- notes cleanup
- small summarization jobs
- status reporting
- lightweight file classification
- non-urgent text transformations

### 8.6 General node contract

Any node may expose:

- OpenAI-compatible model endpoint
- Hermes agent
- opencode CLI
- auto-ingest worker
- voice sidecar
- file processing service

AssistX should delegate based on registered capability, availability, and benchmark history rather than hard-coded host assumptions.

---

## 9. Voice authentication and authorization model

Sophia should separate:

1. speaker identity/authentication,
2. action authorization,
3. response voice selection.

### 9.1 Speaker classes

Supported speaker states:

- `authenticated_scott`
- `scott_voice_unverified`
- `registered_user_authenticated`
- `registered_user_unverified`
- `unknown_speaker`
- `admin_voice_override`

### 9.2 Unknown speaker registration

Unknown speakers should be able to register themselves as users.

Registration should not grant high-impact permissions automatically. It should create a pending or limited user profile until Scott approves expanded permissions.

### 9.3 Scott clone response policy

Sophia should respond using Scott's voice for non-Scott voices as well.

Response voice selection should be independent from action authorization.

Default:

```yaml
voice_response_policy:
  tts_voice_when_scott_authenticated: scott_clone
  tts_voice_when_scott_unverified: scott_clone
  tts_voice_when_registered_user: scott_clone
  tts_voice_when_unknown: scott_clone
```

### 9.4 Morning voice / unauthenticated Scott override

Scott's voice may fail authentication when his voice changes, such as first thing in the morning.

Add an override mechanism named `admin-voice` for now.

Policy:

- If voice auth does not recognize Scott but the override password/passphrase is supplied, classify as `admin_voice_override`.
- Log the override event.
- Allow the session to proceed under Scott-equivalent authority or a slightly restricted Scott authority, depending on final policy.
- Store enough metadata to audit why the override was accepted.

### 9.5 Authorization policy

Scott-authenticated voice sessions may auto-approve low-risk actions without a popup.

All other voices require Scott approval for actions.

Suggested policy table:

| Speaker/Auth State | Ask Questions | Register User | Submit Notes | Low-Risk Actions | High-Risk Actions |
|---|---:|---:|---:|---:|---:|
| `authenticated_scott` | yes | yes | yes | auto-approve | approval or explicit confirmation |
| `admin_voice_override` | yes | yes | yes | auto-approve or fast-confirm | approval or explicit confirmation |
| `registered_user_authenticated` | yes | n/a | yes | requires Scott approval | requires Scott approval |
| `registered_user_unverified` | limited | n/a | yes | requires Scott approval | requires Scott approval |
| `unknown_speaker` | limited | yes | limited | requires Scott approval | requires Scott approval |

### 9.6 Low-risk action examples

Potential low-risk actions:

- create a note
- draft text
- summarize local context
- search memory
- list tasks
- create a draft task
- queue a non-destructive ingest scan
- ask a model endpoint for analysis

High-risk actions still requiring approval or explicit confirmation:

- deleting files
- modifying important records
- sending external messages
- changing network/system configuration
- running shell commands with destructive potential
- moving/renaming large data sets
- exposing data outside the local network
- changing auth/security policy

---

## 10. Real-time demo path

The first end-to-end demo should focus on the real-time AssistX path, not the historical auto-ingest batch path.

Target demo:

```text
1. User speaks to Sophia.
2. Sophia performs VAD/STT/speaker auth.
3. Sophia sends quick input to AssistX.
4. AssistX classifies intent and creates authoritative task state.
5. AssistX delegates execution to a suitable local node.
6. The worker/Hermes/opencode/model endpoint completes the task.
7. AssistX records AgentRun/ToolCall/Artifact/Memory updates.
8. Sophia speaks the response using Scott clone voice.
```

This should run fully offline assuming LAN/Tailscale, local models, local Neo4j, and local storage are reachable.

---

## 11. Historical memory enrichment path

The second path is periodic memory enrichment.

Target flow:

```text
1. auto-ingest runs weekly/monthly or on demand.
2. New dashcam/audio/bodycam/source files are scanned.
3. Transcripts and metadata are normalized.
4. Music/noise/media segments are classified and downgraded.
5. Memory-bearing segments are summarized/extracted.
6. Candidate memories are reviewed or auto-promoted based on confidence.
7. Unified Neo4j `neo4j` memory graph is updated.
8. Embeddings are generated for searchable memory.
9. AssistX retrieval improves for future real-time tasks.
```

This makes auto-ingest the historical graph-of-knowledge builder about Scott, not the live voice/action execution loop.

---

## 12. Artifact and NAS strategy

The NAS should ideally be available to other nodes in the system.

### 12.1 Binary artifacts

NAS/filesystem remains source of truth for:

- raw audio
- dashcam video
- bodycam video
- birdcam clips
- extracted training clips
- generated transcripts
- model artifacts
- reports
- evidence-grade/protected files

### 12.2 Graph metadata

Neo4j stores metadata and relationships:

- artifact ID
- source path
- storage root
- optional host/container path mappings
- sha256 when practical
- retention class
- source task/event
- derived transcript/summary/entity relationships

### 12.3 Path representation

Because paths differ between hosts and containers, store multiple path forms when useful:

```yaml
artifact_id: string
storage_root: nas1 | s-drive | local-ssd | external-drive
relative_path: path/under/root/file.ext
host_path: /media/scott/NAS1/...
container_path: /nas/...
tailscale_host_hint: x1-370
sha256: optional
retention_class: ephemeral | keep | protected | evidence
```

Do not rely on one absolute path being valid from every node.

---

## 13. Delegation policy

AssistX should select delegation targets by:

1. required capability,
2. node availability,
3. model endpoint status,
4. power/cost profile,
5. benchmark results,
6. task risk,
7. data locality.

Preferred routing examples:

- low-power machines: drafts, notes, light summaries
- demo/demo-1: fast model delegation and occasional GPU-heavy work
- deathstar-XPS-8920: legacy auto-ingest continuity and Hermes-side model work
- x1-370: central knowledge/control, heavy orchestration, high-memory jobs
- mini-pc-22: future always-on services once stabilized

---

## 14. Required shared implementation contracts

Create these shared contract docs next:

```text
docs/swarm_contracts/database_unification.md
docs/swarm_contracts/task_authority.md
docs/swarm_contracts/voice_auth_policy.md
docs/swarm_contracts/artifact_paths.md
docs/swarm_contracts/node_registry.md
docs/swarm_contracts/model_endpoint_registry.md
docs/swarm_contracts/auto_ingest_memory_enrichment.md
docs/swarm_contracts/event_envelope.md
```

Minimum schemas to implement:

- `SwarmNode`
- `ServiceEndpoint`
- `Capability`
- `ModelEndpoint`
- `StorageRoot`
- `HealthCheck`
- `VoiceAuthDecision`
- `RegisteredSpeaker`
- `ArtifactRef`
- `MemoryCandidate`
- `IngestBatchReview`

---

## 15. Next build order

### Phase 1 — Documentation and contract lock

- Commit this `UNIFICATION.md` to all three repos.
- Convert this into contract docs under `docs/swarm_contracts/`.
- Update prior integration plans to point here as the current source of truth.

### Phase 2 — AssistX authority implementation

- Add authoritative task-state definitions.
- Add swarm node registry.
- Add model endpoint registry.
- Add event ingestion endpoint.
- Add voice quick-input endpoint or formalize existing endpoint.

### Phase 3 — Sophia quick-input path

- Add `auth_state` and `admin-voice` override flow.
- Add unknown speaker registration path.
- Emit quick-input events to AssistX.
- Speak AssistX responses with Scott clone voice.

### Phase 4 — Delegation agents

- Register Hermes/opencode/model endpoints as node capabilities.
- Route low-power draft tasks to lower-power nodes.
- Route fast generation tasks to demo/demo-1.
- Keep x1-370 as high-context/high-control node.

### Phase 5 — auto-ingest historical memory

- Add ingest batch review summaries.
- Classify dashcam transcript segments.
- Generate embeddings for useful memory nodes.
- Push/migrate Sophia and auto-ingest memory into unified `neo4j` database.

### Phase 6 — Fully offline demo

- Tailscale-only routing.
- Local model endpoint.
- Sophia voice input.
- AssistX task creation/delegation.
- Local execution.
- Sophia spoken response.
- Audit trail in Neo4j.

---

## 16. Open decisions still remaining

1. Should `memory` be fully migrated into `neo4j`, or retained as a voice staging DB with sync jobs?
2. Should `admin-voice` be a spoken passphrase, typed PIN, device-bound token, or combination?
3. Which exact actions count as low-risk for Scott auto-approval?
4. Which node should host the first canonical AssistX control-plane service?
5. Which local DNS names should be added to Pi-hole first?
6. Should FalkorDB be deployed soon as a working-set cache experiment, or deferred until after the first voice demo?
7. Which embedding model should become canonical for unified memory?
8. What is the first acceptance test for unknown speaker registration?

---

## 17. Current north star

The system should become an offline, Scott-centered, graph-memory operating layer where:

- Scott can speak naturally to Sophia.
- Sophia can authenticate or safely handle unknown speakers.
- AssistX owns task state and policy.
- local nodes execute delegated work.
- auto-ingest periodically enriches Scott's historical memory.
- Neo4j unifies long-term context.
- everything critical can operate over Tailscale/LAN without public cloud dependency.
