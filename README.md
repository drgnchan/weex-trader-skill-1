# weex-trader-skill

`weex-trader-skill` is a **WEEX contract-only** skill for Codex / Openclaw / Claude Code.

It is designed for low-friction agent workflows:

- inspect market, account, position, and order state
- open positions with structured single-order or batch-order commands
- close positions and cancel active or conditional orders with explicit scope
- change leverage, margin mode, and isolated margin safely
- inspect contract bills / income with structured filters
- place and modify TP/SL and conditional orders

This repository no longer supports spot automation.

## Internal Layout

The public entrypoint stays the same:

```bash
python3 scripts/weex_contract_api.py ...
```

Internally the skill is now split into focused modules:

- `scripts/weex_contract/core.py`
- `scripts/weex_contract/read_ops.py`
- `scripts/weex_contract/order_ops.py`
- `scripts/weex_contract/account_ops.py`
- `scripts/weex_contract/cli.py`

This keeps command behavior stable while reducing the amount of code an agent has to load and patch at once.

## One-Time Setup

Private endpoints require a WEEX API key with contract permissions.

```bash
export WEEX_API_KEY="..."
export WEEX_API_SECRET="..."
export WEEX_API_PASSPHRASE="..."
export WEEX_API_BASE="https://api-contract.weex.com"
export WEEX_LOCALE="en-US"
```

Security notes:

- never commit API credentials
- use least-privilege keys
- rotate credentials immediately if they leak

## Install In Codex

```text
Help me install this skill: https://github.com/drgnchan/weex-trader-skill
```

Then verify:

```text
Check whether $weex-trader-skill is installed.
```

## How To Use

Mention `$weex-trader-skill` and describe the goal in plain language.

Examples:

```text
Use $weex-trader-skill to show my BTCUSDT contract positions and open orders.
```

```text
Use $weex-trader-skill to open a small ETHUSDT short with a limit order at 10000.
```

```text
Use $weex-trader-skill to close my ETHUSDT contract position.
```

```text
Use $weex-trader-skill to set ETHUSDT isolated leverage to 15x on both sides.
```

## Core Commands

Inspect state:

```bash
python3 scripts/weex_contract_api.py ticker --symbol BTCUSDT --pretty
python3 scripts/weex_contract_api.py account-snapshot --symbol BTCUSDT --pretty
python3 scripts/weex_contract_api.py positions --pretty
python3 scripts/weex_contract_api.py open-orders --symbol BTCUSDT --pretty
python3 scripts/weex_contract_api.py pending-orders --symbol BTCUSDT --pretty
python3 scripts/weex_contract_api.py contract-bills --symbol BTCUSDT --limit 20 --pretty
```

Open a position:

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

Open multiple positions in one request:

```bash
python3 scripts/weex_contract_api.py place-orders-batch \
  --symbol ETHUSDT \
  --batch-orders '[{"intent":"OPEN_LONG","type":"MARKET","quantity":"0.001"},{"intent":"OPEN_SHORT","type":"LIMIT","quantity":"0.001","price":"2100"}]' \
  --confirm-live \
  --pretty
```

Close a position:

```bash
python3 scripts/weex_contract_api.py close-positions \
  --symbol ETHUSDT \
  --confirm-live \
  --pretty
```

Cancel open orders:

```bash
python3 scripts/weex_contract_api.py cancel-open-orders \
  --symbol ETHUSDT \
  --confirm-live \
  --pretty
```

Cancel conditional orders:

```bash
python3 scripts/weex_contract_api.py cancel-pending-orders \
  --symbol ETHUSDT \
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

Set leverage:

```bash
python3 scripts/weex_contract_api.py set-leverage \
  --symbol ETHUSDT \
  --margin-type ISOLATED \
  --value 15 \
  --confirm-live \
  --pretty
```

Change margin mode:

```bash
python3 scripts/weex_contract_api.py set-margin-mode \
  --symbol ETHUSDT \
  --margin-type ISOLATED \
  --position-mode SEPARATED \
  --confirm-live \
  --pretty
```

Adjust isolated margin:

```bash
python3 scripts/weex_contract_api.py adjust-position-margin \
  --symbol ETHUSDT \
  --position-side LONG \
  --amount 20 \
  --direction INCREASE \
  --confirm-live \
  --pretty
```

Place TP/SL or conditional orders:

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

- raw mutating endpoint calls are disabled
- account-wide close/cancel requires explicit `--all`
- `place-order` blocks risky opposite-side combinations unless `--allow-position-reduction` is explicit
- `place-orders-batch` applies the same protection to each order and caps the request at 10 orders
- `set-margin-mode` refuses by default when the symbol still has active positions or open/pending orders
- `adjust-position-margin` resolves isolated positions explicitly and refuses ambiguous target selection
- mutating commands require `--confirm-live`, or `--dry-run` for preview
- business success is checked after HTTP success; the script does not treat `200 OK` as enough

## Current Scope

The contract wrapper is structured around these areas:

- market data
- account state
- positions
- active orders
- conditional orders
- contract leverage / margin-mode management
- isolated margin adjustments
- contract income / bills queries
- batch order placement / cancellation
- dedicated TP/SL workflows

## Regenerate Local Definitions

To rebuild local WEEX contract REST definitions from the current WEEX docs:

```bash
pip install -r requirements-docgen.txt
python3 scripts/generate_weex_api_definitions.py --product contract
```
