---
name: weex-trader-skill
description: Use when the user wants low-friction WEEX contract trading automation via structured REST commands, including market/account inspection, order execution, cancel/close, leverage changes, margin-mode changes, and TP/SL workflows.
metadata:
  version: "2.0.0"
---

# WEEX Contract Trader Skill

This skill is **contract only**.
Do not use it for spot.

Use:
- `scripts/weex_contract_api.py`

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
- For full exits, prefer `close-positions` instead of sending the opposite order side manually.
- For symbol-wide destructive actions, require the user's instruction to be explicit about the symbol.
- For account-wide destructive actions, require the user's instruction to be explicit about `all`.
- Use `--dry-run` when the user wants a preview. Otherwise use `--confirm-live` for live mutation commands.

## Fast Path

```bash
python3 scripts/weex_contract_api.py ticker --symbol BTCUSDT --pretty
python3 scripts/weex_contract_api.py account-snapshot --symbol BTCUSDT --pretty
python3 scripts/weex_contract_api.py positions --pretty
python3 scripts/weex_contract_api.py open-orders --symbol BTCUSDT --pretty
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

## Safety Model

- Raw mutating endpoint access is disabled.
- `place-order` blocks risky opposite-side pairs unless `--allow-position-reduction` is explicit.
- `set-margin-mode` blocks by default when the symbol still has open positions, active orders, or pending orders.
- `close-positions` and `cancel-open-orders` are symbol-scoped by default. Account-wide action requires `--all`.
- Mutating requests always require `--confirm-live`, unless the caller intentionally uses `--dry-run`.
- Successful HTTP is not treated as enough. The script checks business-level success flags and reports failures.

## References

- `references/contract-api-definitions.json`
- `references/contract-api-definitions.md`
- `references/contract-endpoints.md`
- `references/auth-and-signing.md`
- `references/websocket.md`
