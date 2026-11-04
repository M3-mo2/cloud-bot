# UI 07 — Admin panel

The admin panel is reached with `/admin`. It is gated on identity
(`CLOUDBOT_ADMIN_IDS`), never on the run mode; see
[`../ADMIN-PANEL.md`](../ADMIN-PANEL.md) for the architecture and the extension
guide. This document records the screens' behaviour and copy conventions.

---

## 1. Entry and invisibility

- `/admin` opens the overview. A non-admin's `/admin` produces **nothing** — the
  same as an unknown command. There is no "you are not an admin" message.
- Every panel callback is namespaced `adm:`; the gate swallows `adm:` callbacks
  from a non-admin.
- `/notifications` is a shortcut to the notification settings (also gated by
  identity).

The panel is a **menu of registered sections**; the overview offers the sections
that are `in_menu` (Users, Notifications, Audit log). The section list is
generated from `cloudbot/presentation/screens/admin/`'s registry, not written by
hand.

---

## 2. Screens

| Screen | Title key | Notes |
| --- | --- | --- |
| Overview | `admin.title` | engine health first; unknown fleet numbers are labelled, never shown as 0 |
| Users | `admin.users.title` | paged, search, honest empty state |
| User page | `admin.user.title` | identity, workspaces + usage, actions, notification scope, recent activity |
| Entered workspace | reuse of `ws.*` screens | banner + Leave added by the render seam |
| Notifications | `admin.notify.title` | per-category toggles, all switch, pause, cursor, scope |
| Audit log | `admin.audit.title` | paged, filterable |

---

## 3. Entered mode

While entered, **every** screen the admin reaches carries a persistent
`<blockquote>` banner naming the workspace id and owner id, and a **Leave**
button. The banner is added in `handlers/render.show_screen` when
`ctx.entered_ws_id` is set — one seam, so reused user screens are covered without
a per-screen change.

Actions taken in entered mode are recorded in the audit log as performed by the
admin, on that workspace. The log keeps the actor (`admin:<id>`) and the subject
(`tg_id`/`ws_id`) apart.

---

## 4. Destructive confirmations

An admin acting on **someone else's** workspace sees a confirmation generated
from the action's declaration (`confirmation_screen`). The body always states the
subject by name **and** id (`admin.action.<key>.confirm_body` with `{subject}`).
Delete is `DANGER`; suspend is `PRIMARY` (reversible).

---

## 5. Localization and formatting

- Every string is in both catalogs; Arabic is rendered `is_rtl=True`.
- Integers, percentages, sizes and dates go through
  `presentation/i18n/formatters.py`; ids stay ASCII in `<code>`.
- The event taxonomy (categories, summaries, actors) is declared once in
  `cloudbot/domain/events.py` and rendered from catalog keys — no raw token ever
  reaches the screen.
