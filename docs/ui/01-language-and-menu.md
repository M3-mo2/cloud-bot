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

## 0. Navigation — the main menu is the only hub

**There is no reply keyboard. Every button is an inline button.** The **main
menu** is the only screen that offers the account's destinations. **Every other
screen outside a workspace carries exactly one nav entry, the home destination
`nav:menu`** — the workspaces list, the wallet, settings, help and the language
picker included. It is rendered by one helper (`presentation/screens/nav.py`),
so every screen agrees.

**Layout rule (a future screen follows it).** The nav block has **at most two
buttons per row and at most three rows**, and a screen's own primary action may
sit **full width above** it. No screen anywhere uses more than two columns.

The home destination wears **one** label everywhere:

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 🏠 الصفحة الرئيسية | 🏠 Home | inline | primary | `nav:menu` |

The menu's own body (and nothing else) offers:

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| 📦 مساحاتي | 📦 My workspaces | inline | primary | `ws:list` |
| 💳 المحفظة | 💳 Wallet | inline | primary | `pay:wallet` |
| ⚙️ الإعدادات | ⚙️ Settings | inline | primary | `nav:settings` |

**A nav entry never points at the screen you are already on: the current
destination is omitted, not muted (R1).** A disabled entry is still a
button-shaped thing that wastes a grid slot and reads as a destination, so it is
simply absent. On the menu itself no home entry appears (the menu *is* home).

```
[ ➕ مساحة جديدة ]                         (success, full width — the screen's own action)
[ 📦 مساحاتي ] [ 💳 المحفظة ]
[ ⚙️ الإعدادات ]                            ← the menu's body, always last
```

On any other outside screen the block is one row:

```
[ 🏠 الصفحة الرئيسية ] / [ 🏠 Home ]        ← `nav:menu`
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

The body is **one compact line per workspace**, not a four-column table: the name,
its state, and the one number that matters (running). A user with more than five
workspaces is **never silently truncated** — the sixth and beyond are counted in
a visible "and N more" line.

```html
<h2>🤖 CloudBot</h2>
<p>Hello, <b>sara</b> 👋</p>
<hr>
<p>2 workspaces</p>
<p><b>demo</b> — 🟢 Active · 1 running</p>
<p><b>scraper</b> — 🟠 Paused</p>
<p><i>Open My workspaces to enter one, or start a new one.</i></p>
```

### 2.2 Arabic (`is_rtl=True`)

```html
<h2>🤖 CloudBot</h2>
<p>مرحبًا، sara 👋</p>
<hr>
<p>مساحتان</p>
<p><b>demo</b> — 🟢 نشِطة · 1 قيد التشغيل</p>
<p><b>scraper</b> — 🟠 متوقفة مؤقتًا</p>
<p><i>افتح «مساحاتي» للدخول إلى مساحة، أو ابدأ واحدة جديدة.</i></p>
```

> Workspace names are user-supplied; they are escaped, so an RTL name cannot
> break the layout.

### 2.3 Menu keyboard

Full-width primary action, then the menu's own destinations (§0). Help and
Language are no longer here — they moved under Settings.

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| ➕ مساحة جديدة | ➕ New Workspace | inline | success | `ws:new`, full width |
| 📦 مساحاتي | 📦 My workspaces | inline | primary | `ws:list` |
| 💳 المحفظة | 💳 Wallet | inline | primary | `pay:wallet` |
| ⚙️ الإعدادات | ⚙️ Settings | inline | primary | `nav:settings` |

```
[ ➕ مساحة جديدة ]
[ 📦 مساحاتي ] [ 💳 المحفظة ]
[ ⚙️ الإعدادات ]
```

There is no persistent reply menu. **Help and Language are not on the menu** —
they live under Settings (§4). "My workspaces" is reachable as the
**📦 My workspaces** destination, and it wears that one name on the menu.

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
<p>Tap <b>New Workspace</b> and you're in.</p>
```

### Arabic (`is_rtl=True`)

```html
<h2>☁️ CloudBot</h2>
<p>بيئات تطوير واستضافة سحابية — من هاتفك بكل سهولة.</p>
<p>أنشئ مساحة عمل، أضف مشروعك، واتركه يعمل في السحابة.
بلا حاسوب، بلا إعدادات، بلا تعقيد.</p>
<hr>
<p>جاهز للبدء؟</p>
<p>اضغط <b>مساحة جديدة</b> وابدأ فورًا.</p>
```

### Keyboard

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| ➕ مساحة جديدة | ➕ New Workspace | inline | success | opens the create wizard (`ws:new`) |
| 📦 مساحاتي | 📦 My workspaces | inline | primary | `ws:list` |
| 💳 المحفظة | 💳 Wallet | inline | primary | `pay:wallet` |
| ⚙️ الإعدادات | ⚙️ Settings | inline | primary | `nav:settings` |

The board deliberately avoids architecture jargon (no "isolated", "sandbox",
"secure"): it speaks to what the user gets, not how the engine works. The inline
New Workspace button is kept (his `b606238` fix); the shared nav block follows it.


---

## 3. `/help`

A rich message that explains the product to a new user: what a workspace is (and
that it is isolated), what the tool screens do, that a user may keep several
workspaces and several terminals, and — the part that was previously documented
nowhere the user can see — how to change the terminal size from inside the shell.
The command list stays in a collapsible block so the screen stays short.

### English

```html
<h2>❓ Help</h2>
<p>CloudBot runs your code safely in an isolated sandbox — you never need to touch a server.</p>
<p>A <b>workspace</b> is your private sandbox: its own files, packages, processes,
   ports and terminals, isolated from every other user. Everything you do to it
   stays inside it.</p>
<p>Inside a workspace you can browse and edit <b>files</b>, <b>run</b> commands and
   jobs, open a <b>terminal</b>, install <b>packages</b>, publish <b>tunnels</b>, and
   inspect <b>ports</b>, <b>metrics</b>, <b>processes</b> and <b>archives</b>.</p>
<p>You can keep several workspaces and open several terminals in each. A terminal
   is live until you close it or it sits idle long enough — then it ends and you
   can open a new one.</p>
<hr>
<h3>Terminal size</h3>
<p>The chat decides how much of the screen you see. To change the terminal itself,
   run <code>stty cols N rows M</code> inside the shell (<code>stty size</code>
   reads the current size).</p>
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
<p>يشغّل CloudBot أكوادك بأمان داخل بيئة معزولة — دون أن تتعامل مع أي خادم.</p>
<p><b>مساحة العمل</b> هي بيئتك الخاصة: ملفاتها ومكتباتها وعملياتها ومنافذها وطرفياتها،
   معزولة عن كل مستخدم آخر. كل ما تفعله يبقى داخلها.</p>
<p>داخل مساحة العمل يمكنك تصفح <b>الملفات</b> وتحريرها، و<b>تشغيل</b> الأوامر
   والمهام، وفتح <b>طرفية</b>، وتثبيت <b>المكتبات</b>، ونشر <b>الأنفاق</b>، وفحص
   <b>المنافذ</b> و<b>المقاييس</b> و<b>العمليات</b> و<b>الأرشيفات</b>.</p>
<p>يمكنك الاحتفاظ بعدة مساحات عمل وفتح عدة طرفيات في كل مساحة. تبقى الطرفية حيّة
   حتى تغلقها أو تبقى خاملة بما يكفي — عندها تنتهي ويمكنك فتح واحدة جديدة.</p>
<hr>
<h3>حجم الطرفية</h3>
<p>الدردشة تحدّد ما تراه من الشاشة. لتغيير الطرفية نفسها، نفّذ
   <code>stty cols N rows M</code> داخل الصدفة (<code>stty size</code> يقرأ الحجم
   الحالي).</p>
<details><summary>الأوامر</summary>
  <ul>
    <li><code>/start</code> — فتح القائمة</li>
    <li><code>/settings</code> — اللغة والتفضيلات</li>
    <li><code>/help</code> — هذه الرسالة</li>
  </ul>
</details>
<p><i>ملفاتك خاصة بك وحدك. لا شيء مشترك بين المستخدمين.</i></p>
```

Keyboard: the single nav entry, **🏠 Home** (`nav:menu`). Help is a page, not a
hub, so it repeats no account destination. There is no Back/reply button.

---

## 4. `/settings`

Settings is a **two-entry hub**: Help and Language, and nothing else but the
home entry.

### English

```html
<h2>⚙️ Settings</h2>
<p>Help and language.</p>
```

### Arabic (`is_rtl=True`)

```html
<h2>⚙️ الإعدادات</h2>
<p>المساعدة واللغة.</p>
```

### Keyboard

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| ❓ مساعدة | ❓ Help | inline | primary | `nav:help` |
| 🌐 اللغة | 🌐 Language | inline | primary | `nav:language` |
| 🏠 الصفحة الرئيسية | 🏠 Home | inline | primary | `nav:menu` |

---

## 5. Language picker (from Settings)

Same card as onboarding, but reached mid-session. The **current** language is
shown as a `disabled` label so the user sees their state (that disabled label is
the language *choice*, not a nav destination), and the single home entry (§0)
follows.

### English (current = EN)

| Label (AR) | Label (EN) | Type | Style | Flags |
| --- | --- | --- | --- | --- |
| العربية | — | inline | primary | `lang:ar` |
| — | ✓ English | inline | primary | `lang:en`, **disabled** |
| 🏠 الصفحة الرئيسية | 🏠 Home | inline | primary | `nav:menu` |

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
