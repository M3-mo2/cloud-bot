# Sending Rich HTML Messages in aiogram (Bot API 10.1+)

This guide explains how to send richly formatted messages using HTML with aiogram's `send_rich_message` method. This feature, introduced in **Telegram Bot API 10.1** (June 2026), allows bots to send highly structured content like headings, tables, lists, media, and more—far beyond the basic formatting supported by `parse_mode="HTML"`.

## 1. Core Concepts & Requirements

*   **Method:** `bot.send_rich_message()`
*   **Payload Type:** `InputRichMessage`
*   **aiogram Version:** Requires **aiogram 3.29.1 or higher** (the feature was added in 3.29.0). It is recommended to use the latest version (3.31.0+) for full support and bug fixes.
*   **Mutually Exclusive Fields:** You must provide **exactly one** of `html`, `markdown`, or `blocks` in your `InputRichMessage` object.

## 2. The `InputRichMessage` Object

The `InputRichMessage` object is the container for your rich message content.

```python
from aiogram.types import InputRichMessage

rich_msg = InputRichMessage(
    html: str | None = None,          # HTML-formatted content
    markdown: str | None = None,      # Markdown-formatted content
    is_rtl: bool | None = None,       # Set True for right-to-left languages
    skip_entity_detection: bool | None = None, # Disable auto-detection of URLs, mentions, etc.
    blocks: list | None = None,       # Structured content as a list of blocks
    media: list | None = None         # Media referenced via tg:// links in HTML
)
```


## 3. Sending a Rich HTML Message

To send a rich message with HTML, instantiate `InputRichMessage` with the `html` field and pass it to `bot.send_rich_message()`.

### Basic Example

```python
from aiogram import Bot
from aiogram.types import InputRichMessage

async def send_rich_html(bot: Bot, chat_id: int):
    html_content = """
    <h1>Welcome to Rich Messages</h1>
    <p>This is a paragraph with <b>bold</b>, <i>italic</i>, and <code>inline code</code>.</p>
    <ul>
        <li>First item</li>
        <li>Second item</li>
    </ul>
    <pre><code class="language-python">print("Hello, World!")</code></pre>
    """

    rich_message = InputRichMessage(html=html_content)

    await bot.send_rich_message(
        chat_id=chat_id,
        rich_message=rich_message
    )
```


### Using a Dictionary (Quick Shortcut)

You can also pass a dictionary directly to the `rich_message` parameter:

```python
await bot.send_rich_message(
    chat_id=chat_id,
    rich_message={"html": "<h1>Title</h1><p>Body text</p>"}
)
```


## 4. Supported HTML Formatting Options

Rich HTML supports a wide range of tags for advanced formatting. Below is a summary of the most important elements:

| Feature | HTML Tag / Syntax |
| :--- | :--- |
| **Headings** | `<h1>`, `<h2>`, `<h3>`, ... up to `<h6>` |
| **Paragraphs** | `<p>` |
| **Bold / Italic** | `<b>`, `<i>` |
| **Underline / Strikethrough** | `<u>`, `<s>` |
| **Inline Code** | `<code>` |
| **Preformatted Block** | `<pre><code class="language-python">...</code></pre>` |
| **Unordered List** | `<ul><li>...</li></ul>` |
| **Ordered List** | `<ol><li>...</li></ol>` |
| **Task List** | `<ul>` with `<input type="checkbox">` (see Telegram docs) |
| **Block Quotation** | `<blockquote>` |
| **Divider** | `<hr>` |
| **Table** | `<table>`, `<tr>`, `<td>`, `<th>` |
| **Collapsible Block** | `<details><summary>...</summary>...</details>` |
| **Media (Images, Video, Audio)** | `<img src="https://...">`, or `tg://photo?id=...` for uploaded files |
| **Mathematical Expressions (LaTeX)** | `$x^2 + y^2$` (inline), `$$...$$` (block) |

> **Note:** For a complete and authoritative list of supported HTML tags and attributes, always refer to the official Telegram Bot API documentation: [Rich Message Formatting Options](https://core.telegram.org/bots/api#rich-message-formatting-options).

## 5. Important Limits & Considerations

Rich messages are subject to specific limits:
*   **Text Length:** Up to **32,768 UTF-8 characters** (including custom emoji alt text and formula source).
*   **Blocks:** Up to **500 blocks** (including nested blocks, list items, table rows, etc.).
*   **Nesting:** Up to **16 levels** of nested formatting and blocks.
*   **Media Attachments:** Up to **50** total.
*   **Table Columns:** Up to **20 columns**.

> **Note:** When using HTML links, Telegram clients will display an "Open this link?" confirmation before opening inline URLs.

## 6. Key Differences from `parse_mode="HTML"`

| Aspect | `parse_mode="HTML"` (Legacy) | `send_rich_message` (Rich HTML) |
| :--- | :--- | :--- |
| **Method** | `bot.send_message(..., parse_mode="HTML")` | `bot.send_rich_message(..., rich_message=InputRichMessage(html=...))` |
| **Formatting Scope** | Basic: bold, italic, links, inline code, blockquotes | **Advanced:** headings, tables, task lists, collapsible blocks, LaTeX, media blocks, etc. |
| **Payload** | Plain string with HTML tags | Structured `InputRichMessage` object |
| **Introduced In** | Long-standing feature | Bot API 10.1 (June 2026) |

## 7. References

*   **aiogram `InputRichMessage` Documentation:** [https://docs.aiogram.dev/en/dev-3.x/api/types/input_rich_message.html](https://docs.aiogram.dev/en/dev-3.x/api/types/input_rich_message.html)
*   **aiogram `send_rich_message` Documentation:** [https://docs.aiogram.dev/en/dev-3.x/api/methods/send_rich_message.html](https://docs.aiogram.dev/en/dev-3.x/api/methods/send_rich_message.html)
*   **Telegram Bot API - Rich Messages:** [https://core.telegram.org/bots/api/rich-messages](https://core.telegram.org/bots/api/rich-messages)
*   **aiogram Changelog (v3.29.0 - Rich Messages Added):** [https://docs.aiogram.dev/en/v3.29.1/changelog.html](https://docs.aiogram.dev/en/v3.29.1/changelog.html)
