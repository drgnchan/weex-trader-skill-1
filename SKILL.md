---
name: weex-trader-skill
description: Use when the user wants low-friction WEEX contract trading automation via structured REST commands, including market/account inspection, single or batch order execution, cancel/close, leverage and isolated-margin changes, income queries, and TP/SL workflows.
metadata:
  version: "2.0.0"
---

# WEEX Contract Trader Skill

This skill is **contract only**.
Do not use it for spot.

Use:
- `scripts/weex_contract_api.py`

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
- For symbol-wide destructive actions, require the user's instruction to be explicit about the symbol.
- For account-wide destructive actions, require the user's instruction to be explicit about `all`.
- Use `--dry-run` when the user wants a preview. Otherwise use `--confirm-live` for live mutation commands.

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
- `references/websocket.md`
