# UI 01 — Language & Main Menu

This document shows the actual rich HTML that is sent, and the exact keyboards.
Arabic output is always sent with **`is_rtl=True`**; this is noted once here and
applies to every Arabic example in all UI documents.

Conventions used in this document:
- Rich output is shown as the exact HTML passed in `InputRichMessage(html=...)`.
- Keyboards are shown as tables: **label (AR / EN) · type · style · flags**.
- Callback tokens are abbreviated (`…`); real ones are ≤ 64 bytes (see
  [`../rendering/keyboards.md`](../rendering/keyboards.md) §8).

---

## 0. Navigation — one inline block on every top-level screen

**There is no reply keyboard. Every button is an inline button.** Navigation lives
in a single shared block at the **bottom** of every top-level screen — the main
menu, the welcome board, `/help`, `/settings`, the workspaces list and the
language picker. It is rendered by one helper (`presentation/screens/nav.py`) so
every screen agrees.

**Layout rule (a future screen follows it).** The nav block has **at most two
buttons per row and at most three rows**, and a screen's own primary action may
sit **full width above** it. No screen anywhere uses more than two columns.

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 📦 المساحات | 📦 Workspaces | inline | primary | `ws:list` |
| ⚙️ الإعدادات | ⚙️ Settings | inline | primary | `nav:settings` |
| 🌐 اللغة | 🌐 Language | inline | primary | `nav:language` |
| ❓ مساعدة | ❓ Help | inline | primary | `nav:help` |
| 🏠 القائمة | 🏠 Menu | inline | primary | `nav:menu` — only where the screen is not the menu |

**A nav entry never points at the screen you are already on: the current
destination is omitted, not muted (R1).** A disabled entry was the old answer; it
is wrong because a disabled button is still a button-shaped thing that wastes a
grid slot and reads as a destination. On the menu itself no Menu button appears;
on the workspaces list `ws:list` and Menu are both dropped, because the list
*is* `ws:list` and the menu's body duplicates the list (R2). The block is thus 2
rows on the menu (its four destinations), 2 rows on Help/Settings/Language (their
destinations + Menu, minus themselves), and 2 rows on the workspaces list (its
three remaining destinations).

```
[ ➕ مساحة جديدة ]                         (success, full width — the screen's own action)
[ 📦 المساحات ] [ ⚙️ الإعدادات ]
[ 🌐 اللغة ]     [ ❓ مساعدة ]              ← the nav block, always last
```

---

## 1. First contact — `/start` with no stored user

The bot has no session yet. Before it can speak, it asks the user to choose a
language. To be readable by anyone, the prompt is short and the two buttons are
each written **in their own script**.

### Rendered rich HTML

```html
<h2>🌐 Choose your language</h2>
<p>اختر لغتك</p>
<p>You can change this later from the menu.</p>
<p>يمكنك تغييرها لاحقًا من القائمة.</p>
```

Sent with `is_rtl=False` (the card is inherently bilingual).

### Keyboard

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 🇸🇦 العربية | — | inline | primary | — |
| — | 🇬🇧 English | inline | primary | — |

```python
InlineKeyboardMarkup(inline_keyboard=[[
    inline("العربية", "lang:ar", intent=PRIMARY),
    inline("English", "lang:en", intent=PRIMARY),
]])
```

> The language labels are **not** translated — `العربية` reads "العربية" and
> English reads "English" no matter the current locale.

### After the user taps `العربية`

The same message is **edited in place** into the menu, now in Arabic with
`is_rtl=True`, carrying that screen's inline nav block. No reply keyboard is ever
sent.

---

## 2. The main menu

The menu is the home screen. It is a rich message with a greeting and a live
snapshot, a full-width **New Workspace** action, and the shared inline nav block
(§0) below it. Its own navigation no longer lives in a reply keyboard.

### 2.1 English

```html
<h2>🤖 CloudBot</h2>
<p>Hello, <b>@sara</b> 👋</p>
<hr>
<p>You have <b>2 workspaces</b>.</p>
<table is_bordered="true">
  <tr><th>Workspace</th><th>Status</th><th>Runs</th><th>Disk</th></tr>
  <tr><td><code>demo</code></td><td>Active</td><td>1 active</td><td>2.3 MB / 256 MB</td></tr>
  <tr><td><code>scraper</code></td><td>Paused</td><td>—</td><td>12 MB / 512 MB</td></tr>
</table>
<p><i>Tap a workspace to open it, or create a new one.</i></p>
```

### 2.2 Arabic (`is_rtl=True`)

```html
<h2>🤖 CloudBot</h2>
<p>مرحبًا، <b>@sara</b> 👋</p>
<hr>
<p>لديك <b>مساحتان</b>.</p>
<table is_bordered="true">
  <tr><th>المساحة</th><th>الحالة</th><th>التشغيلات</th><th>القرص</th></tr>
  <tr><td><code>demo</code></td><td>نشِطة</td><td>١ نشِط</td><td>٢٫٣ ميغابايت / ٢٥٦ ميغابايت</td></tr>
  <tr><td><code>scraper</code></td><td>متوقفة مؤقتًا</td><td>—</td><td>١٢ ميغابايت / ٥١٢ ميغابايت</td></tr>
</table>
<p><i>اضغط على مساحة لفتحها، أو أنشئ واحدة جديدة.</i></p>
```

> Workspace names are user-supplied; they are placed in `<code>` and escaped, so
> an RTL name inside the Arabic table cannot break the layout.

### 2.3 Menu keyboard

Full-width primary action, then the shared inline nav block (§0). The layout is
rebuilt from the old three-full-width-row version to spend the rows it saves on
the distributed block.

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| ➕ مساحة جديدة | ➕ New Workspace | inline | success | `ws:new`, full width |
| 📦 المساحات | 📦 Workspaces | inline | primary | `ws:list` |
| ⚙️ الإعدادات | ⚙️ Settings | inline | primary | `nav:settings` |
| 🌐 اللغة | 🌐 Language | inline | primary | `nav:language` |
| ❓ مساعدة | ❓ Help | inline | primary | `nav:help` |

```
[ ➕ مساحة جديدة ]
[ 📦 المساحات ] [ ⚙️ الإعدادات ]
[ 🌐 اللغة ]     [ ❓ مساعدة ]
```

There is no persistent reply menu. "My workspaces" is reachable as the
**📦 Workspaces** destination in the nav block.

With no workspaces, the menu is replaced by the **welcome board** (§2.5).

---

## 2.5 The welcome board (first run / no workspaces)

When the user owns no workspaces — a brand-new user, or one who deleted them
all — the main menu is **not** shown. Instead a dedicated welcome board states
what the product is, gives the promise in one line, and offers a single obvious
action.

### English

```html
<h2>☁️ CloudBot</h2>
<p>Development environments and cloud hosting — right from your phone.</p>
<p>Create a workspace, add your project, and keep it running in the cloud.
No laptop. No setup. No hassle.</p>
<hr>
<p>Ready to begin?</p>
<p>Press <b>Start</b> and you're in.</p>
```

### Arabic (`is_rtl=True`)

```html
<h2>☁️ CloudBot</h2>
<p>بيئات تطوير واستضافة سحابية — من هاتفك بكل سهولة.</p>
<p>أنشئ مساحة عمل، أضف مشروعك، واتركه يعمل في السحابة.
بلا حاسوب، بلا إعدادات، بلا تعقيد.</p>
<hr>
<p>جاهز للبدء؟</p>
<p>اضغط <b>ابدأ</b> وابدأ فورًا.</p>
```

### Keyboard

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| ▶️ ابدأ | ▶️ Start | inline | success | opens the create wizard (`ws:new`) |
| 📦 المساحات | 📦 Workspaces | inline | primary | `ws:list` |
| ⚙️ الإعدادات | ⚙️ Settings | inline | primary | `nav:settings` |
| 🌐 اللغة | 🌐 Language | inline | primary | `nav:language` |
| ❓ مساعدة | ❓ Help | inline | primary | `nav:help` |

The board deliberately avoids architecture jargon (no "isolated", "sandbox",
"secure"): it speaks to what the user gets, not how the engine works. The inline
Start button is kept (his `b606238` fix); the shared nav block follows it.


---

## 3. `/help`

A rich message explaining the bot, using a collapsible block for the command list
so the screen stays short.

### English

```html
<h2>❓ Help</h2>
<p>CloudBot runs your code safely in an isolated sandbox.</p>
<ul>
  <li>Create a <b>workspace</b> — your private sandbox.</li>
  <li>Add <b>files</b> and <b>run</b> code.</li>
  <li>Watch output live, and stop anything you start.</li>
</ul>
<details><summary>Commands</summary>
  <ul>
    <li><code>/start</code> — open the menu</li>
    <li><code>/settings</code> — language &amp; preferences</li>
    <li><code>/help</code> — this message</li>
  </ul>
</details>
<p><i>Your files are private to you. Nothing is shared between users.</i></p>
```

### Arabic (`is_rtl=True`)

```html
<h2>❓ مساعدة</h2>
<p>يشغّل CloudBot أكوادك بأمان داخل بيئة معزولة.</p>
<ul>
  <li>أنشئ <b>مساحة عمل</b> — بيئتك الخاصة المعزولة.</li>
  <li>أضف <b>ملفات</b> و<b>شغّل</b> الأكواد.</li>
  <li>راقب المخرجات مباشرة، وأوقف أي شيء تبدأه.</li>
</ul>
<details><summary>الأوامر</summary>
  <ul>
    <li><code>/start</code> — فتح القائمة</li>
    <li><code>/settings</code> — اللغة والتفضيلات</li>
    <li><code>/help</code> — هذه الرسالة</li>
  </ul>
</details>
<p><i>ملفاتك خاصة بك وحدك. لا شيء مشترك بين المستخدمين.</i></p>
```

Keyboard: the shared inline nav block (§0) with **Help omitted** (we are on it —
R1), so the block is **Workspaces · Settings**, then **Language · Menu**. There is
no Back/reply button.

---

## 4. `/settings`

### English

```html
<h2>⚙️ Settings</h2>
<p><b>Language:</b> English</p>
<p><b>Plan:</b> Free</p>
<hr>
<p>Choose what to change.</p>
```

### Arabic (`is_rtl=True`)

```html
<h2>⚙️ الإعدادات</h2>
<p><b>اللغة:</b> العربية</p>
<p><b>الخطة:</b> مجانية</p>
<hr>
<p>اختر ما تريد تغييره.</p>
```

### Keyboard

The shared inline nav block (§0) with **Settings omitted** (we are on it — R1).
Changing the language is the **Language** destination (`nav:language`), and
**Menu** returns home.

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 📦 المساحات | 📦 Workspaces | inline | primary | `ws:list` |
| 🌐 اللغة | 🌐 Language | inline | primary | `nav:language` |
| ❓ مساعدة | ❓ Help | inline | primary | `nav:help` |
| 🏠 القائمة | 🏠 Menu | inline | primary | `nav:menu` |

---

## 5. Language picker (from Settings)

Same card as onboarding, but reached mid-session. The **current** language is
shown as a `disabled` label so the user sees their state (that disabled label is
the language *choice*, not a nav destination), and the shared inline nav block
(§0) with **Language omitted** (we are on it — R1) follows.

### English (current = EN)

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| العربية | — | inline | primary | `lang:ar` |
| — | ✓ English | inline | primary | `lang:en`, **disabled** |
| 📦 المساحات | 📦 Workspaces | inline | primary | `ws:list` |
| ⚙️ الإعدادات | ⚙️ Settings | inline | primary | `nav:settings` |
| ❓ مساعدة | ❓ Help | inline | primary | `nav:help` |
| 🏠 القائمة | 🏠 Menu | inline | primary | `nav:menu` |

Switching language edits the current message into the menu in the new locale.

---

## 6. Transitions

| From | Action | Result |
| --- | --- | --- |
| `/start` (returning) | — | one `ReplyKeyboardRemove` (legacy migration, §7), then the menu |
| `/start` (new) | tap لغتي | edit language card → menu (new locale) |
| Language card | tap a language | edit into menu in new locale, persist choice |
| Any top-level screen | tap a nav destination | edit into that screen |
| Anywhere | `/settings` | settings screen |
| Anywhere | `/help` | help screen |

---

## 7. The one-time legacy-keyboard migration

An inline keyboard cannot remove a **persistent reply keyboard** a user already
has installed from an older build — it would stay stuck at the bottom of the chat
forever. So on `/start`, and **only** there, a returning user receives exactly one
extra message carrying `ReplyKeyboardRemove` (an invisible zero-width body),
before the menu. It never displaces the screen's inline keys, fires at most once
per `/start`, and never appears from `/help`, `/settings`, or any nav callback.
New users never had a reply keyboard, so it is a harmless no-op for them. See
`docs/DECISIONS.md` CB-26.
