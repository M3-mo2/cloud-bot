# Admin panel (`/admin`)

The admin panel is a first-class, extensible part of the bot. This document is
its **extension guide**: a future change should be possible from here alone.

The panel is **identity-gated**, not mode-gated. Only a Telegram id listed in
`CLOUDBOT_ADMIN_IDS` can reach it — in **every** run mode, including
`PRODUCTION`. Being an admin is an identity, not a mode.

---

## The two invariants

1. **The gate is one seam.** Admin identity is checked in exactly one place:
   `cloudbot/middleware/admin_gate.py`. No handler re-checks `is_admin`; a new
   section or action relies on the gate. An architecture test fails if `is_admin`
   appears outside the allowed seam files
   (`tests/architecture/test_layers.py::test_the_admin_gate_is_the_only_identity_seam`).
2. **Screens are pure.** A screen is a pure function of `(AdminView, locale)` —
   no I/O, no engine, no DB. `AdminService` builds the `AdminView` read model;
   screens only render it. This is what keeps them snapshot-testable.

A non-admin must not be able to tell the panel exists: `/admin` for a non-admin
is swallowed by the gate and produces nothing — the same response as any unknown
command.

The **existing** DEV/DEBUG diagnostics (`/diag`, `/health`, `/mode`, `/raw`,
`/replay`) are left exactly as they were: they keep the mode gate and are not
recognised by the identity gate. The panel is a separate seam beside them, not a
loosening of them.

---

## How to add a **section**

A section is data, not an `if` branch. The main menu is generated from the
registry, so adding a section touches no menu code.

1. Create `cloudbot/presentation/screens/admin/<name>.py`.
2. Build a pure screen function `def my_screen(view: AdminView) -> Screen:`.
3. Register it:

   ```python
   from .section import AdminSection, register

   register(
       AdminSection(
           key="my_section",          # unique; used in the ``adm:sec:my_section`` callback
           title_key="admin.section.my_section",  # a catalog key, both locales
           order=50,                  # menu order
           icon="🧩",
           build=my_screen,           # Callable[[AdminView], Screen]
       )
   )
   ```

4. Import the module in `cloudbot/presentation/screens/admin/__init__.py` (that
   import is what registers it).
5. Add `admin.section.my_section` to **both** `en.py` and `ar.py`.

That is the whole change. The menu (`overview.py::_menu_markup`) reads
`registry.menu()`; `tests/unit/test_admin_registry.py` proves a newly registered
section appears without touching menu code.

If the section needs data, add a field to `AdminView` (in
`cloudbot/domain/admin.py`) and populate it in the handler's `_view(...)` helper /
`AdminService`.

---

## How to add an **action**

Actions are data too. A new action is one entry plus one handler — no screen code
and no bespoke confirmation screen.

1. Add an entry to `ACTIONS` in
   `cloudbot/presentation/screens/admin/actions.py`:

   ```python
   AdminAction(
       key="restart",                    # ``adm:restart:<subject>`` / ``adm:ask:restart:<subject>``
       label_key="admin.action.restart",
       intent=Intent.PRIMARY,
       confirm=True,                     # dangerous ⇒ a confirmation is generated
       confirm_title_key="admin.action.restart.confirm_title",
       confirm_body_key="admin.action.restart.confirm_body",
   )
   ```

2. Add the label strings (`admin.action.restart`, `...confirm_title`,
   `...confirm_body`) to both catalogs.
3. Put a button on a screen with
   `action_button(action("restart"), subject, locale)`.
4. Add the handler in `cloudbot/handlers/admin_panel.py`:

   ```python
   @router.callback_query(F.data.startswith("adm:restart:"))
   async def on_restart(cb, ctx, services, sender, events):
       ws_id = cb.data.split(":", 2)[2]
       owner = await services.admin.owner_of(WorkspaceId(ws_id))
       await services.admin.restart_workspace(WorkspaceId(ws_id))
       events.admin_action(admin_id=ctx.actor_tg_id, action="restart", ws_id=ws_id, owner_tg_id=owner)
       ...
   ```

A **dangerous** action (`dangerous=True`) gets its confirmation screen generated
from the same declaration by `confirmation_screen(...)`, and the confirmation
always names *whose* data it affects.

---

## How to add an **event category**

1. Add a member to `EventCategory` in `cloudbot/domain/events.py`. Its value is
   the stored key; `label_key` is derived (`event.cat.<value>`).
2. Add the default severity in `_DEFAULT_SEVERITY` (same module).
3. Add the label to both catalogs under `event.cat.<value>`.
4. Add a small emit method to `RequestEvents`
   (`cloudbot/services/events.py`) — e.g. `def my_thing(...)`, mirroring the
   others.
5. Call it from a handler at the point the thing **completed** (the middleware
   flushes intents after the handler returns).

Nothing in the notifier or the dispatcher changes: the category is data, the
delivery arithmetic is generic, and a new category is on by default.

---

## What the panel contains

| Section | Callback | Shows |
| --- | --- | --- |
| Overview | `/admin` | user counts, active 24 h, workspaces by status, live runs/sessions, engine health, bot uptime, queue state. An unreachable engine is labelled **unknown, not zero**. |
| Users | overview menu | paged list (id, name, locale, plan, created/last seen, workspace count), search by id/name, honest empty state. |
| User page | a user row | identity, workspaces with live usage from `/{id}/usage`, quick actions, notification scope for that user, recent activity. |
| Workspace (entered) | an *Enter* action | the **same** workspace screens the user sees, with a persistent banner and a way out. |
| Notifications | overview menu / `/notifications` | per-category toggles, an *all* switch, pause/resume, delivery cursor, per-user scope. |
| Audit log | overview menu / a user page | paged, filterable by category/actor/subject; the same table the notifier reads. |

### Entering a workspace

From a user page, **Enter** puts the admin inside the workspace. The admin then
navigates the **existing** user screens (files, execution, terminals, packages,
tunnels, ports, metrics, processes, archives) — they are reused, not
re-implemented. A persistent banner names the workspace on every screen and a
**Leave** button exits.

Every interaction in entered mode is recorded in the audit log **attributed to
the admin (`admin:<id>`), on that workspace** — never to the user. That rule is
enforced in one place, the event source middleware
(`cloudbot/middleware/events_source.py`).

### The ownership exemption

An admin reads other users' data by design. That exemption is expressed **once**,
in `OwnershipService.assert_admin_override` (with a comment explaining why), and
called only from `AdminService`. It is not sprinkled as `if is_admin` through the
code. An architecture test asserts both facts.

---

## Notifications

Events are persisted in the bot's own SQLite (`events`), written through **one
seam** (`EventSourceMiddleware` + `EventsService`): handlers record the *intent*
(`events.workspace_created(...)`) and the middleware flushes it after the handler
succeeds. A transient engine failure becomes `engine.unreachable` from the error
guard, into the same buffer.

Per-admin state (`event_subscriptions`, `admin_notify_state`) holds subscriptions,
a delivery cursor, a pause flag and an optional per-user scope.

`NotifierService` is pure arithmetic: given the log and the admin's state it
produces a `NotificationPlan`. The rules:

- **Coalesce per (admin, category)** within `COALESCE_WINDOW_S` (60 s).
- **Cap** at `MAX_MESSAGES_PER_POLL` messages per flush. When hit, **one**
  message states the real number of suppressed **events**; nothing is queued
  unboundedly and nothing is dropped silently.
- **Gap**: a backlog larger than `GAP_THRESHOLD` (200), or one accumulated while
  paused, becomes **one** summary message rather than a replay.
- **Backwards clock**: if the cursor is ahead of the log, it is reset to the tip
  and the admin is told, rather than silently skipping.
- **Paused**: no delivery and no cursor advance; resuming reports the backlog as
  a gap.

The cursor advances only after every send in the plan succeeded, so a restart
neither loses nor duplicates an event.
