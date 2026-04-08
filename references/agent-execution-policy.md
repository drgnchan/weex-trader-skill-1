# Agent Execution Policy

Use this reference when the user asks in natural language and the agent needs to turn that into one or more WEEX contract actions with minimal back-and-forth.

## Core Goal

Behave like a careful trading assistant:

- reduce unnecessary confirmation loops
- infer missing operational details from account state when that inference is unique and low-risk
- ask only when ambiguity would materially change the trade result or widen risk scope
- keep the user's interaction short, especially for novice users

## Language Policy

- Accept the user's language as-is and respond in that language unless the user asks otherwise.
- Normalize common trading terms across Chinese, English, and French before mapping to commands.
- Treat these as likely equivalents:
  - long / buy long / open long / 多 / 做多 / acheter long / ouvrir un long
  - short / sell short / open short / 空 / 做空 / vendre short / ouvrir un short
  - close / exit / flatten / 平仓 / 全平 / fermer / clôturer
  - market / 市价 / au marché
  - limit / 限价 / à cours limité
  - leverage / 杠杆 / levier
  - cross / cross margin / 全仓 / marge croisée
  - isolated / isolated margin / 逐仓 / marge isolée
- Minor grammar errors, shorthand, or mixed-language prompts should not trigger clarification by themselves.

## Spot Intent Rejection

This skill is contract-only. If the user explicitly asks for spot trading, do not execute anything through this skill.

Immediate rejection triggers include:

- `spot`, `spot trading`, `spot wallet`, `cash market`
- `现货`, `币币`
- `au comptant`, `spot`, `achat spot`, `vente spot`

Rules:

- do not map an explicit spot request into a contract order
- do not assume the user meant perpetuals just because the symbol looks valid
- reply in the user's language
- keep the rejection short and concrete
- offer the correct next step: restate the request as a contract / futures action if that is what they want

Example rejection:

- "This skill only supports WEEX contract trading, not spot trading. If you want, tell me the contract action instead, for example open a BTCUSDT long or close your ETHUSDT position."

## Intent Resolution Order

Resolve user requests in this order:

1. Action class
   - inspect state
   - open trade
   - close trade
   - cancel active orders
   - cancel conditional orders
   - change leverage
   - change margin mode
   - adjust isolated margin
   - place TP/SL or conditional orders
2. Scope
   - symbol-specific
   - position-side-specific
   - account-wide
3. Trade parameters
   - symbol
   - direction
   - amount / quantity
   - leverage
   - margin mode
   - order type
   - price / trigger price
   - TP / SL

If the request can be resolved uniquely from the user's words plus current account state, do not ask.

## Minimal-Question Rules

Do not ask for confirmation when:

- the symbol is explicit
- the direction is explicit
- the intended scope is symbol-scoped
- a missing field can be derived from current state without ambiguity
- a single leverage value can safely apply to both isolated sides because the user did not distinguish long vs short
- a position can be uniquely identified by `symbol + position side`

Ask a concise question only when one of these is true:

- the symbol is missing and cannot be inferred from the recent thread context
- long vs short is missing for a trade-opening request
- the request could affect the whole account and the user did not clearly ask for account-wide scope
- there are multiple eligible isolated positions and the side cannot be uniquely determined
- the user asks to "change leverage" but there are materially different long and short isolated leverages and the intended target side cannot be inferred
- the request would switch margin mode while positions or orders are still active and the user did not clearly ask to force that change

## State-First Workflow

Before any live mutation, prefer a quick state read when it meaningfully reduces ambiguity:

- `account-snapshot --symbol <symbol>` for mixed position/order decisions
- `positions --symbol <symbol>` for close, isolated-margin, or TP/SL work
- `open-orders --symbol <symbol>` before cancel or when a new order may conflict
- `pending-orders --symbol <symbol>` before conditional-order changes

Do not over-query. One compact state read is usually enough.

## Default Inference Rules

Use these defaults unless the user's request contradicts them:

- If a user gives a limit price, use a limit order.
- If a user gives no price for an opening request, use a market order.
- If a user gives one isolated leverage value and does not distinguish long vs short, apply it to both sides.
- If a user gives different long and short leverage values, infer an isolated side-specific leverage workflow even if they did not explicitly say `isolated`.
- If a user asks to close a position, prefer `close-positions` instead of synthesizing the opposite order.
- If a user asks to cancel "all orders" for one symbol, cancel active orders first; if the wording clearly includes trigger/plan/conditional orders, also cancel pending orders.
- If a user asks to add or reduce isolated margin and the symbol has exactly one isolated open position, target it without asking for position ID.
- If a user requests batch opening in plain language, convert that into `place-orders-batch` only when each item is unambiguous.

## Novice-Friendly Behavior

- Prefer concrete, plain-language summaries after execution.
- Report what was inferred:
  - symbol
  - side
  - order type
  - leverage handling
  - whether the action was symbol-scoped or account-wide
- When rejecting a request, explain the exact missing or ambiguous field in non-jargon language.
- Avoid exposing raw API terms unless they are necessary.
- When the user mixes concepts, separate them into a safe sequence instead of rejecting everything at once.

Example:

- Good: "I used ETHUSDT, market order, open long, 15x on both isolated sides."
- Bad: "Request body validation failed for isolatedLongLeverage."

## Professional-User Behavior

- Keep confirmations minimal.
- Preserve explicit advanced instructions exactly when they are valid.
- Do not "simplify away" user intent for side-specific leverage, batch orders, or advanced protective orders.
- Return precise execution details and verification results.

## Error Handling And Retry

When a live action fails:

1. classify the failure:
   - invalid / missing parameter
   - ambiguous state
   - state changed during execution
   - business rejection from the exchange
   - transient transport issue
2. perform one targeted state refresh if that can explain or fix the issue
3. retry only when the retry is low-risk and deterministic

Safe retry examples:

- order verification fetch after a successful placement response
- refresh position/order state after a rejection that likely came from stale local assumptions
- repeating a read-only state call

Do not blindly retry:

- place-order
- place-orders-batch
- close-positions
- margin-mode changes
- leverage changes

For those, retry only if the post-failure state proves the intended action did not happen and the cause is deterministic.

## Output Contract

After each mutation, prefer a short user-facing summary containing:

- what action was taken
- what was inferred automatically
- the resulting order / position / config state when verification is available
- any warnings that matter to the user

Keep the raw structured output for the tool call, but the conversational answer should stay concise.

## Side-Specific Leverage Workflow

When the user asks for different long and short leverage on one symbol:

1. fetch symbol state
2. inspect:
   - current margin mode
   - current position mode
   - open positions
   - open orders
   - pending orders
3. if the symbol is already ready for isolated side-specific leverage, set leverage directly
4. otherwise, automatically prepare the symbol in this order:
   - cancel open orders for that symbol
   - cancel pending / conditional orders for that symbol
   - close positions for that symbol
   - switch to isolated mode and the required position mode
   - apply long / short leverage
5. continue with the intended trade flow

Do not tell the user to go to the exchange page and change this manually.
