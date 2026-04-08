---
name: weex-trader-skill
description: Use when the user wants low-friction WEEX contract trading automation via structured REST commands, including market/account inspection, single or batch order execution, cancel/close, leverage and isolated-margin changes, income queries, and TP/SL workflows.
metadata:
  version: "2.2.0"
---

# WEEX Contract Trader Skill

This skill is **contract only**.
Do not use it for spot.

Use:
- `scripts/weex_contract_api.py`

Load this when the user prompt is natural-language, multilingual, novice-style, shorthand, or ambiguous:
- `references/agent-execution-policy.md`

Implementation layout:
- `scripts/weex_contract/core.py`: shared client, request execution, payload analysis, cached state, symbol/order helpers
- `scripts/weex_contract/read_ops.py`: read-only account and market inspection commands
- `scripts/weex_contract/order_ops.py`: active orders, batch orders, conditional orders, TP/SL, close flows
- `scripts/weex_contract/account_ops.py`: leverage, margin-mode, isolated margin, auto-append margin
- `scripts/weex_contract/cli.py`: parser and command dispatch
- `scripts/weex_contract_api.py`: thin compatibility entrypoint

For private endpoints:

```bash
export WEEX_API_KEY="..."
export WEEX_API_SECRET="..."
export WEEX_API_PASSPHRASE="..."
export WEEX_API_BASE="https://api-contract.weex.com"
export WEEX_LOCALE="en-US"
```

## Agent Policy

- Prefer the structured commands below. Do not use raw JSON mutation calls.
- Ask only for missing fields that are necessary to avoid an unsafe or ambiguous trade.
- For opening trades, prefer `place-order --intent OPEN_LONG|OPEN_SHORT`.
- For multiple openings in one request, use `place-orders-batch` with structured JSON objects, not raw endpoint mutation.
- For full exits, prefer `close-positions` instead of sending the opposite order side manually.
- For isolated margin changes, use `adjust-position-margin` and resolve the target position by `--symbol` plus `--position-side` whenever possible.
- When the user requests different long and short leverage for one symbol, treat that as an isolated side-specific leverage workflow.
- For side-specific leverage workflows, do not send the user to the exchange UI. Inspect symbol state and complete the required symbol preparation through this skill.
- For symbol-wide destructive actions, require the user's instruction to be explicit about the symbol.
- For account-wide destructive actions, require the user's instruction to be explicit about `all`.
- Use `--dry-run` when the user wants a preview. Otherwise use `--confirm-live` for live mutation commands.

## Conversation Policy

- Follow the user's language. Chinese, English, French, and mixed-language trading prompts should be accepted without translation requests.
- Infer standard trading synonyms from natural language: long/short, market/limit, cross/isolated, close/flatten, TP/SL.
- Prefer one compact state read before a live action when it removes ambiguity.
- Do not ask the user to restate information that is already present in the prompt or can be uniquely derived from current account state.
- If one isolated leverage value is given without long/short separation, apply it to both isolated sides.
- If one isolated position can be uniquely matched by symbol and side, do not ask for position ID.
- If the user gives different long and short leverage values, infer isolated side-specific leverage even if they did not explicitly say `isolated`.
- For novice users, explain what was inferred in plain language after execution.
- For professional users, preserve explicit advanced instructions exactly and keep confirmations minimal.

## Spot Rejection Policy

- This skill must not execute spot requests.
- If the user clearly asks for spot or cash trading, stop immediately and explain that this skill only supports WEEX contract trading.
- Do not silently reinterpret an explicit spot request as a contract request.
- Ask the user to restate the request as a contract / futures action only if they want that.

Treat these as explicit spot signals:

- `spot`, `cash`, `spot wallet`
- `现货`, `币币`
- `achat spot`, `vente spot`, `comptant`
- requests to simply buy and hold the coin without leverage or contract context, when the user explicitly says it is spot

Suggested response shape:

- say this skill is contract-only
- say spot trading is unsupported here
- ask the user to restate the goal as a contract action if needed

## Clarification Threshold

Ask a short clarifying question only when:

- symbol is missing and cannot be inferred from recent context
- long vs short is missing for an opening request
- a request may act on the whole account but the user did not clearly ask for account-wide scope
- multiple isolated positions match and side is not uniquely determined
- isolated long/short leverage targets cannot be inferred safely
- switching margin mode would affect active positions or orders and the user did not clearly ask to force it

Otherwise, infer and proceed.

## Side-Specific Leverage Workflow

When the user asks for different long / short leverage on one symbol:

1. Read current symbol state.
2. If the symbol is already isolated and the position mode already supports side-specific leverage, set leverage directly.
3. If the symbol is cross margin or not in the required position mode:
   - cancel open orders for that symbol
   - cancel pending / plan / conditional orders for that symbol
   - close positions for that symbol
   - switch the symbol to isolated mode and the required position mode
   - apply the requested long / short leverage
4. Continue with the user's order flow after the leverage workflow completes.

Do not ask the user to manually configure margin mode or leverage in the exchange UI.
Use `--dry-run` only when the user asked for a preview. Otherwise complete the sequence.

## Recovery Policy

- After failures, prefer one targeted state refresh before asking the user anything.
- Retry reads freely.
- Retry writes only when post-failure state proves the intended action did not happen and the retry is low-risk and deterministic.
- Do not blindly replay open, close, leverage, or margin-mode mutations.

## Fast Path

```bash
python3 scripts/weex_contract_api.py ticker --symbol BTCUSDT --pretty
python3 scripts/weex_contract_api.py account-snapshot --symbol BTCUSDT --pretty
python3 scripts/weex_contract_api.py positions --pretty
python3 scripts/weex_contract_api.py open-orders --symbol BTCUSDT --pretty
python3 scripts/weex_contract_api.py contract-bills --symbol BTCUSDT --limit 20 --pretty
```

## Open A Position

```bash
python3 scripts/weex_contract_api.py place-order \
  --symbol ETHUSDT \
  --intent OPEN_SHORT \
  --type LIMIT \
  --quantity 0.001 \
  --price 10000 \
  --confirm-live \
  --pretty
```

## Close A Position

```bash
python3 scripts/weex_contract_api.py close-positions \
  --symbol ETHUSDT \
  --confirm-live \
  --pretty
```

## Cancel Open Orders

```bash
python3 scripts/weex_contract_api.py cancel-open-orders \
  --symbol ETHUSDT \
  --confirm-live \
  --pretty
```

## Cancel Pending Orders

```bash
python3 scripts/weex_contract_api.py cancel-pending-orders \
  --symbol ETHUSDT \
  --confirm-live \
  --pretty
```

## Change Leverage

Cross:

```bash
python3 scripts/weex_contract_api.py set-leverage \
  --symbol ETHUSDT \
  --margin-type CROSSED \
  --value 20 \
  --confirm-live \
  --pretty
```

Isolated, same leverage on both sides:

```bash
python3 scripts/weex_contract_api.py set-leverage \
  --symbol ETHUSDT \
  --margin-type ISOLATED \
  --value 15 \
  --confirm-live \
  --pretty
```

Isolated, side-specific:

```bash
python3 scripts/weex_contract_api.py set-leverage \
  --symbol ETHUSDT \
  --margin-type ISOLATED \
  --long 10 \
  --short 5 \
  --confirm-live \
  --pretty
```

## Change Margin Mode

```bash
python3 scripts/weex_contract_api.py set-margin-mode \
  --symbol ETHUSDT \
  --margin-type ISOLATED \
  --position-mode SEPARATED \
  --confirm-live \
  --pretty
```

By default, the script refuses to switch margin mode when active positions or orders exist for that symbol.

## Adjust Isolated Margin

Increase isolated margin for the long side:

```bash
python3 scripts/weex_contract_api.py adjust-position-margin \
  --symbol ETHUSDT \
  --position-side LONG \
  --amount 20 \
  --direction INCREASE \
  --confirm-live \
  --pretty
```

## TP/SL Workflows

Place a dedicated TP/SL plan order:

```bash
python3 scripts/weex_contract_api.py place-tpsl-order \
  --symbol ETHUSDT \
  --plan-type STOP_LOSS \
  --trigger-price 1700 \
  --quantity 0.001 \
  --position-side LONG \
  --confirm-live \
  --pretty
```

Place a conditional entry order:

```bash
python3 scripts/weex_contract_api.py place-conditional-order \
  --symbol ETHUSDT \
  --intent OPEN_LONG \
  --conditional-type STOP_MARKET \
  --quantity 0.001 \
  --trigger-price 1900 \
  --confirm-live \
  --pretty
```

## Batch Orders

Place multiple orders in one request:

```bash
python3 scripts/weex_contract_api.py place-orders-batch \
  --symbol ETHUSDT \
  --batch-orders '[{"intent":"OPEN_LONG","type":"MARKET","quantity":"0.001"},{"intent":"OPEN_SHORT","type":"LIMIT","quantity":"0.001","price":"2100"}]' \
  --confirm-live \
  --pretty
```

Cancel multiple active orders:

```bash
python3 scripts/weex_contract_api.py cancel-orders-batch \
  --order-ids 12345,12346 \
  --confirm-live \
  --pretty
```

## Safety Model

- Raw mutating endpoint access is disabled.
- `place-order` blocks risky opposite-side pairs unless `--allow-position-reduction` is explicit.
- `place-orders-batch` reuses the same side / position-side safety checks per item and caps a request at 10 orders.
- `set-margin-mode` blocks by default when the symbol still has open positions, active orders, or pending orders.
- `set-leverage` now auto-prepares a symbol for side-specific isolated leverage when that transition is required.
- `adjust-position-margin` resolves isolated positions explicitly and refuses ambiguous side selection.
- `close-positions` and `cancel-open-orders` are symbol-scoped by default. Account-wide action requires `--all`.
- `cancel-pending-orders` is also symbol-scoped by default. Account-wide cancellation requires `--all`.
- Mutating requests always require `--confirm-live`, unless the caller intentionally uses `--dry-run`.
- Successful HTTP is not treated as enough. The script checks business-level success flags and reports failures.

## References

- `references/contract-api-definitions.json`
- `references/contract-api-definitions.md`
- `references/contract-endpoints.md`
- `references/auth-and-signing.md`
- `references/agent-execution-policy.md`
- `references/websocket.md`
