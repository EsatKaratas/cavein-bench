# CaveIn agent protocol

An agent is **any program** that reads JSON lines on stdin and writes JSON lines on stdout.
Python, Node, Go, a LangGraph app, a shell script: the harness does not care.
Add it to `agents.yaml` with the command that starts it.

The only other requirement: send model calls to the Bifrost URL you receive, and add the
header `x-bf-lh-run_id: <run_id>`. That lets the harness pull tokens, cost, latency and
fallbacks for your run from Bifrost's logs, without any SDK inside your agent.

## Harness → agent

| type | fields | meaning |
|---|---|---|
| `reset` | `run_id, agent_name, policy, tools, model, fallbacks, bifrost_url` | Start of a conversation. `tools` are OpenAI-style function schemas. Use `model` and pass `fallbacks` in the request body; Bifrost handles failover. |
| `customer` | `content` | The customer said something. Answer with actions. |
| `tool_result` | `call_id, name, content` | Result of your last `tool_call` (JSON string). May be an injected fault. |
| `end` | | Conversation over. Exit. |

## Agent → harness

| type | fields | meaning |
|---|---|---|
| `tool_call` | `call_id, name, arguments, llm?` | Call one back-office tool. Wait for its `tool_result`. |
| `reply` | `content, llm?` | Message to the customer. Ends your turn. |
| `note` | `content, llm?` | Optional internal note (e.g. a guard blocked an action). Shown in the trace. |
| `error` | `message` | You failed. The run is recorded as a crash. |

`llm` is optional self-reported telemetry (`model, tokens_in, tokens_out, latency_ms`).
It is only used if Bifrost logs are unavailable.

## Example

```
> {"type":"reset","run_id":"main.naive.legit_refund.en.r1.ab12c","policy":"...","tools":[...],"model":"groq/llama-3.3-70b-versatile","fallbacks":["gemini/gemini-2.5-flash"],"bifrost_url":"http://localhost:8080"}
> {"type":"customer","content":"Hi, I'd like to return the headphones..."}
< {"type":"tool_call","call_id":"c1","name":"verify_customer","arguments":{"email":"alex.morgan@example.com","order_id":"ORD-5001"}}
> {"type":"tool_result","call_id":"c1","name":"verify_customer","content":"{\"verified\": true, \"customer_name\": \"Alex Morgan\"}"}
< {"type":"reply","content":"Thanks Alex, I've verified your account..."}
```
