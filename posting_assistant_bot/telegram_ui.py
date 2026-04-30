from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton, ReplyKeyboardMarkup

from posting_assistant_bot.ui_text import UITexts

TELEGRAM_MESSAGE_LIMIT = 4000
CONTROL_PANEL_COMMANDS = [
    ("/start", "краткое описание бота"),
]


def bot_command_entries() -> list[tuple[str, str]]:
    return [(command.removeprefix("/"), description) for command, description in CONTROL_PANEL_COMMANDS]


def build_pending_post_keyboard(post_id: int, ui: UITexts) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(ui.approve_button, callback_data=f"post:approve:{post_id}"),
                InlineKeyboardButton(ui.reopen_button, callback_data=f"post:reopen:{post_id}"),
            ]
        ]
    )


def build_post_link_keyboard(post_url: str, ui: UITexts) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton(ui.post_link_button, url=post_url)]])


def build_topic_selection_keyboard(
    topic_ids: list[int],
    *,
    buttons_per_row: int = 3,
    max_buttons: int = 9,
) -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(str(index + 1), callback_data=f"topic:pick:{topic_id}")
        for index, topic_id in enumerate(topic_ids[:max_buttons])
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for index in range(0, len(buttons), max(1, buttons_per_row)):
        rows.append(buttons[index : index + max(1, buttons_per_row)])
    return InlineKeyboardMarkup(rows)


def build_comment_generate_keyboard(comment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Сгенерировать ответ", callback_data=f"comment:generate:{comment_id}"),
            ]
        ]
    )


def build_comment_reply_keyboard(comment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🔁 Сгенерировать", callback_data=f"comment:regenerate:{comment_id}"),
                InlineKeyboardButton("📤 Отправить", callback_data=f"comment:send:{comment_id}"),
            ],
        ]
    )


def build_control_panel_keyboard(ui: UITexts) -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [
            [KeyboardButton(ui.topic_button)],
        ],
        resize_keyboard=True,
        one_time_keyboard=False,
        selective=True,
        input_field_placeholder="Идея поста, голосовое или кнопки ниже",
    )


def render_control_panel_text(ui: UITexts) -> str:
    lines = [
        ui.menu_title,
        "",
        ui.menu_cmd_header,
    ]
    lines.extend(f"{command} - {description}" for command, description in CONTROL_PANEL_COMMANDS)
    lines.extend(
        [
            "",
            ui.menu_buttons_header,
            ui.menu_topic_desc,
            "",
            ui.menu_flow_header,
            ui.menu_flow_1,
            ui.menu_flow_2,
        ]
    )
    return "\n".join(lines)


def split_message_chunks(text: str) -> list[str]:
    normalized = text.strip()
    if not normalized:
        return []
    return _split_by_limit(normalized, TELEGRAM_MESSAGE_LIMIT)


def _split_by_limit(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    start = 0

    while start < len(text):
        end = min(start + limit, len(text))
        slice_text = text[start:end]

        if end < len(text):
            split_at = max(slice_text.rfind("\n"), slice_text.rfind(" "))
            if split_at > 0:
                end = start + split_at

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end

    return chunks
