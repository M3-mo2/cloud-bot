# The force-join gate — how it decides, and what it records

**Status:** implemented (TASK forcejoin-v2). Read with `MOHAMED.md` (the anti-lockout
taste) and `SCREENS.md` (the screen recipe).

The gate is a middleware: a user who has not joined every enabled chat cannot use
the bot. It is one of three identity seams (admin, ban, force-join), all
registered in `bootstrap/app.py`.

## The rule that must never break

A chat the bot does not administer, a network failure, a rate limit or a deleted
chat yields `unknown` and **lets the user through**. Only a known-absent user is
refused. `MembershipUnknown` lives in the domain and the service never reports an
`unknown` chat as `missing`.

## The decision table (`forcejoin_events`)

One row per membership decision, written by `ForceJoinService` — never by a
handler — so the middleware, the «تحقّقت» button and the admin test are covered by
construction. A cached positive is a row too (`cached=1`, `latency_ms=0`): the
cache is measured, not invisible. A refusal is never cached.

| outcome | reason | when |
|---|---|---|
| `pass` | `subscribed` | a fresh `getChatMember` says member/administrator/creator |
| `pass` | `cached_positive` | the 180 s positive cache answered (`cached=1`, latency 0) |
| `deny` | `absent` | a fresh answer says left/kicked/restricted-without-membership |
| `unknown` | `not_admin` | chat not found, `peer_id_invalid`, `bot is not a member`, `not enough rights`; `is_admin` is set to 0 (the row is **not** disabled) |
| `unknown` | `network` / `bad_request` / `api` | the answer could not be read; the configuration is untouched |
| `error` | `rate_limited` | `429 … retry after N`; that chat is paused for N seconds, the user is admitted |
| `config` | `add` / `remove` / `enable` / `disable` / `test` / `gate_on` / `gate_off` | an operator action, so one table answers both questions |

`detail` is capped at 300 characters and blanked when it looks like a credential
(`Bearer`, `token`, `api_key`, `secret`, `password`).

## The cooldown

A user may re-check one chat at most every 3 s; inside that window the previous
answer is returned with `cached=1`. `force=True` (the admin test and the
«تحقّقت» button) bypasses it. The button additionally renders a disabled state
with the remaining seconds after a press.

## Retention

`SqliteForceJoinEventStore.prune(keep_days=90)` runs once at boot (never a
runner) and logs the row count it removed.

## Statistics

`adapters/persistence/forcejoin_stats.py` reads the same table with SQL only —
gate counts, volume + per-day, outcomes, funnel, per-chat rates, cache hit-rate
and p50/p95 of the uncached call, top refused users, and the health rates. The
panel's `/forcejoin` re-implements those queries in TypeScript against the same
Neon schema. An empty range says "no decisions in this range"; it never renders a
zero that looks like a healthy zero.

## Known residual risk

If the Bot API answers **`left`/`kicked` for a chat where the bot has silently
lost its admin rights** (rather than raising a bad request), the gate records a
`deny` and refuses a user who may in fact be subscribed. Telegram normally raises
`bot is not a member` / `not enough rights` in that state, which the gate maps to
`unknown`; the boot self-check and the health strip surface the drift, but a
`deny` is only as truthful as the API answer behind it.
