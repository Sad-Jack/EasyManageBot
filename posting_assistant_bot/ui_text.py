from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class UITexts:
    topic_button: str
    approve_button: str
    reopen_button: str
    post_link_button: str
    ready_line_1: str
    ready_line_2: str
    ready_line_3: str
    menu_title: str
    menu_cmd_header: str
    menu_buttons_header: str
    menu_topic_desc: str
    menu_flow_header: str
    menu_flow_1: str
    menu_flow_2: str
    topics_header: str
    topics_pick_prompt: str
    approve_success: str
    topic_generating: str


RU_TEXTS = UITexts(
    topic_button="🎯 Генератор тем",
    approve_button="✅ Approve",
    reopen_button="🔁 Reopen",
    post_link_button="К посту",
    ready_line_1="Posting Assistant Bot готов.",
    ready_line_2="Пишите идею текстом или отправляйте голосовое сообщение.",
    ready_line_3="Для публикации используйте ✅ Approve, для нового варианта — 🔁 Reopen.",
    menu_title="Posting Assistant Bot",
    menu_cmd_header="Доступная команда:",
    menu_buttons_header="Кнопки меню:",
    menu_topic_desc="🎯 Генератор тем - получить темы и выбрать одну.",
    menu_flow_header="Сценарий поста:",
    menu_flow_1="1. Отправьте идею текстом или голосом.",
    menu_flow_2="2. Получите черновик с кнопками ✅ Approve / 🔁 Reopen.",
    topics_header="🎯 Темы для поста:",
    topics_pick_prompt="Выбери тему:",
    approve_success="Апрувнуто ✅",
    topic_generating="Генерирую пост...",
)

EN_TEXTS = UITexts(
    topic_button="🎯 Topic Generator",
    approve_button="✅ Approve",
    reopen_button="🔁 Reopen",
    post_link_button="Open Post",
    ready_line_1="Posting Assistant Bot is ready.",
    ready_line_2="Send a text idea or a voice message.",
    ready_line_3="Use ✅ Approve to publish or 🔁 Reopen to regenerate a new draft.",
    menu_title="Posting Assistant Bot",
    menu_cmd_header="Available command:",
    menu_buttons_header="Main buttons:",
    menu_topic_desc="🎯 Topic Generator - get topics and pick one.",
    menu_flow_header="Post flow:",
    menu_flow_1="1. Send an idea by text or voice.",
    menu_flow_2="2. Get draft with ✅ Approve / 🔁 Reopen.",
    topics_header="🎯 Topics for post:",
    topics_pick_prompt="Pick a topic:",
    approve_success="Approved ✅",
    topic_generating="Generating post...",
)


def get_ui_texts(language: str) -> UITexts:
    return EN_TEXTS if language == "en" else RU_TEXTS
