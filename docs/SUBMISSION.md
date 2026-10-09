# Submission — Agent Memory Gateway

Submit at https://tokensand.com/cyberhack/submit before **4:30 PM PT**. Lines marked **[VERIFY]** must be confirmed true before pasting.

## Form fields

**Project name:** Agent Memory Gateway

**Repository:** https://github.com/ffelten/agent-memory-gateway

**Demo video:** <shareable link after recording>

**Live demo page:** http://67s3r5os51agp42v6tfsi3fhsc.ingress.zencloud.eu (hosted on Akash)

**Team:** Florian — florian@autom8.build · Ash — <email>

**Prizes to select:** Top overall (Pi) · Senso · ClickHouse · Akash

## What we built (paste)

Agents now share memory across a company, and that memory leaks. An engineer's agent writes a handoff note for Customer Alpha. Buried in the copied incident context is Alpha's API credential. Another team's agent, working for Customer Beta, searches the shared memory, finds the credential, and can use it to read Alpha's private customer record. (In our demo, a controlled probe on Beta's side performs this step.)

Agent Memory Gateway sits in front of shared agent memory. Every write and read goes through it:

- **Secrets never enter memory.** Each candidate memory is scanned before ingestion. A credential quarantines the whole write before it reaches the knowledge base.
- **Permissions follow the data.** Every memory inherits the access restrictions of the sources it was built from. Search and direct lookups are scoped on the server to what the caller is allowed to read.
- **Scripts can't go around it.** Agents hold only run-scoped gateway tokens, never the backend keys, so the agent's own client and a hand-written Python script hit the same door and get the same decision.
- **Work still gets done.** Beta's agent retrieves the approved public runbook and publishes a source-linked troubleshooting note.
- **Every decision is recorded** as metadata-only audit events. Secrets, queries and tokens are never logged.

We demonstrate the same frozen input twice: without the gateway it causes a real breach, and with the gateway it is quarantined while useful work continues.

## How we used the sponsors (paste, keep honest)

- **Senso:** the shared company memory. Admitted knowledge is ingested and retrieved with scoped search (`require_scoped_ids`), and the gateway checks the provider version before returning text. Live ingest → ready → scoped retrieval → delete round trip verified.
- **AkashML:** inference for both autonomous agents (`openai/gpt-oss-120b`). Real calls verified.
- **Akash Network:** the demo page runs on Akash compute, deployed with the Console API.
- **ClickHouse:** metadata-only audit analytics (decision, reason, transport, latency). Verified end to end: all seven gateway traces from the final live demo run are present in ClickHouse with matching decisions and measured timings, delivered by the deployed gateway's analytics Lambda.
- **AWS:** API Gateway + Lambda policy gateway, DynamoDB for authoritative state, Secrets Manager isolating every backend key from agents. Deployed and verified: the full demo passes all 10 acceptance checks against the live endpoint with real AkashML agents. Both sides use real Senso: the protected side through the gateway, and the unprotected breach baseline through a separate trusted harness with no admission check. The page reads each trace's timings back from ClickHouse (7 of 7 delivered).

## Honest limits (for judges' questions)

- Detection covers the named canary and common credential patterns, not every paraphrased, encoded or fragmented secret. Source permissions carry the main personal-data protection.
- Script parity is demonstrated over HTTP: the agent client and a raw Python script get identical decisions. We have not yet verified the agents' runtime isolation (the claim that they cannot reach backend keys inside their own sandbox) or an actual MCP transport end to end.
- The gateway cannot recall data already returned to an agent. Revocation blocks new reads immediately.
- Memory-defense research exists (MINJA, MemSentry). Our contribution connects customer access, source permissions, script enforcement, useful actions and real sponsor evidence in one working system.

---

## 3-minute video script

Story order: the breach first, then the fix, then the architecture.

| Time | On screen | Say |
|---|---|---|
| 0:00–0:15 | Demo page hero | "Agents share memory now. Here's what goes wrong: same agent, same note, two outcomes." |
| 0:15–0:35 | "The rule" card | "Beta's agent works for Customer Beta. Its key can't open Alpha's records. Access denied." |
| 0:35–1:00 | "The slip" note | "Agent A, working Alpha's incident on AkashML, writes a handoff note to shared memory. The copied incident context contains Alpha's credential." |
| 1:00–1:30 | Red column | "Without a gateway, a controlled probe on Beta's side searches the shared memory in real Senso, finds the note, and uses the key. It reads Alpha's private customer record. That's the breach." |
| 1:30–2:05 | Green column, then terminal run | "Same note, byte for byte, through our gateway. It finds the credential and quarantines the write before Senso ever stores it. Beta finds nothing. A Python script calling the API directly gets the same answer, because agents never hold the backend keys." |
| 2:05–2:25 | "Work still gets done" | "Beta's agent still does its job: it retrieves approved public memory through the gateway and publishes a source-linked note. And the same existing Alpha memory returns 200 for Alpha and 404 for Beta." |
| 2:25–2:45 | Evidence: trace table / ClickHouse | "Every decision is recorded as metadata only in ClickHouse. No secrets, no queries, no tokens." |
| 2:45–3:00 | Architecture line + conclusion | "AWS gateway, Senso memory, AkashML agents, ClickHouse audit, all running live. For these fixtures, the gateway blocks the credential-bearing write, enforces each customer's permissions on every read, and still lets a public-only agent publish useful, sourced work." |

**Recording tips:** record the terminal run of `demo/run_demo.py` against the AWS gateway as B-roll for 1:30–2:05. Pre-record the whole thing; don't depend on live network during the presentation.
